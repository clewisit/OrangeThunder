#!/bin/python3
#// OT817 Orange Thunder test drive
#// FT-817 Transceiver CAT emulator 
#// This program receives commands thru a virtual serial port and produces responses
#// compatible with the Yaesu FT-817 (c) Specification, the transceiver is implemented with
#// the following resources:
#//     - rtlsdrlib to operate as a receiver front-end using a RTL-SDR dongle
#//     - rpitx to operate as a transmission back-end (sendiq mode)
#//     - scripts uses also CSDR, aplay, arecord, pulseaudio and pavucontrol
#// License:
#//   This program is free software: you can redistribute it and/or modify
#//   it under the terms of the GNU General Public License as published by
#//   the Free Software Foundation, either version 2 of the License, or
#//   (at your option) any later version.
#//
#//   This program is distributed in the hope that it will be useful,
#//   but WITHOUT ANY WARRANTY; without even the implied warranty of
#//   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#//   GNU General Public License for more details.
#//
#//   You should have received a copy of the GNU General Public License
#//   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#// lu7did: initial load
#*----------------------------------------------------------------------------
#* Initialization
#* DO NOT RUN EITHER AS A rc.local script nor as a systemd controlled service
#* If you didn't read this line carefully and bricked your RBPI, then you should
#*----------------------------------------------------------------------------
#*-----------------------------------------------------------------------------$
#* Import libraries
#*-----------------------------------------------------------------------------$
import serial
import zipfile
import os
import glob
import sys
import numpy as np
import math
import argparse
import subprocess
import time
import datetime
import binascii
import inspect
import fcntl
import signal
import psutil
#*----------------------------------------------------------------------------
#* Transceiver mode variables
#*----------------------------------------------------------------------------
ft817_modes={ 0x00 : 'LSB',
              0x01 : 'USB',
              0x02 : 'CW',
              0x03 : 'CWR',
              0x04 : 'AM',
              0x08 : 'FM',
              0x0A : 'DIG',
              0x0C : 'PKT'}
#*----------------------------------------------------------------------------
#*  SDR Transceiver commands and process management
#*----------------------------------------------------------------------------
#* Prototype commands
#*----------------------------------------------------------------------------
#cmdRXUSB="rtl_sdr -s 1200000 -f %LO% -D 2 - | csdr convert_u8_f | csdr shift_addition_cc `python -c "print float(%LO%-%FREQ%)/%SAMPLE%"`| csdr fir_decimate_cc 25 0.05 HAMMING | csdr bandpass_fir_fft_cc 0 0.5 0.05 | csdr realpart_cf | csdr agc_ff | csdr limit_ff | csdr convert_f_s16 | aplay -t raw -f S16_LE -c1 -r48000"
cmdRXUSB="rtl_sdr -s 1200000 -f %LO% -D 2 - | csdr convert_u8_f "

cmdTXUSB="arecord -c1 -r48000 -D 
ault -fS16_LE - | csdr convert_i16_f | csdr fir_interpolate_cc 2 | csdr dsb_fc | csdr bandpass_fir_fft_cc 0.002 0.06 0.01 | csdr fastagc_ff | sudo ./sendiq -i /dev/stdin -s 96000 -f %FREQ% -t float"
pRX=None
pTX=None
z=None

#*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=
#*                    PROCESS MANAGEMENT FUNCTIONS
#*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=*=

#*-----------------------------------------------------------------------------
#* non_block_read
#* trick function to read a descriptor with look ahead
#*-----------------------------------------------------------------------------
def non_block_read(output):
    fd = output.fileno()
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
    try:
        return output.read()
    except:
        return ""

#*----------------------------------------------------------------------------
#* Exception and Termination handler
#*----------------------------------------------------------------------------
def killProc(p):

   if p == None:
      log(0,"killProc: process found as None)")
      return
   try:
     parent = psutil.Process(p.pid)
     log(2,"killProc: process %s)" % parent)
     for child in parent.children(recursive=True):  # or parent.children() for recursive=False
       log(2,"killProc:     -- child(%s)" % child)
       child.kill()
     parent.kill()
   except UnboundLocalError as error:
     log(0,"killProc: Unable to kill parent PID")
   except NameError as error:
     log(2,"killProc: Allocation error")
   except Exception as exception:
     pass
#*-----------------------------------------------------------------------------------------------
#* waitProc
#* waiting for a process to start looking for a given string
#*-----------------------------------------------------------------------------------------------
def waitProc(p,stOK,timeout):
    log(2,"waitProc: waiting for string(%s) for timeout(%d)" % (stOK,timeout))
    ts=time.time()
    while (time.time()-ts) <= timeout:
        s=non_block_read(p.stdout)
        if s!="":
           if s.find(stOK) != -1:
              log(2,"waitProc: process (%s) found OK string (%s)" % (str(psutil.Process(p.pid)),stOK))
              return 0
           else:
              log(1,s.replace("\n",""))
    return -1
#*------------------------------------------------------------------------------------------------
#* startProc
#* Start a process with the given command and waits till an Ok string is detected
#*------------------------------------------------------------------------------------------------

def startProc(cmd,stOK):
    log(2,"startProc: cmd(%s) OK(%s)" % (cmd,stOK))
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if waitProc(p,stOK,WAIT_IDLE) == 0:
       log(1,"startProc: Process successfully launched")
       return p
    log(0,"startProc: failed to launch process *ABORT*")
    raise Exception('startProc: general exceptions not caught by specific handling')
    return None
       
#*-----------------------------------------------------------------------------
#* killProcList  (CANDIDATE TO REMOVE)
#* find all matches to a given process name or substring and then kill them all
#*-----------------------------------------------------------------------------
#def killProcList(s):
#    cmd="ps -aux | pgrep %s" % s
#    p = subprocess.Popen(cmd, universal_newlines=True,shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
#    while True:
#       s=p.stdout.readline()
#       if s=='' and p.poll() is not None:
#          break
#       PID=s.replace("\n","")
#       log(0,"killProcList:  -- PID(%s)" % PID)
#       execute("sudo kill -9 %s" % PID)
#    o=p.communicate()[0]
#    e=p.returncode
#    return
#
#*----------------------------------------------------------------------------
#* Exception and Termination handler
#*----------------------------------------------------------------------------
def signal_handler(sig, frame):
   log(0,"signal_handler: Transceiver is being terminated, clean up completed!")
   try:
     if pRX != None:
        killProc(pRX)
        log(1,"signal_handler: Receiver front-end termination completed")
   except:
     log(0,"signal_handler: unable to kill receiver front-end")
   try:
     if pTX != None:
        killProc(pTX)
        log(1,"signal_handler: Transmitter encoder termination completed")
   except:
     log(0,"signal_handler: unable to kill transmitter encoder")
   
   try:
     killProc(z)
     log(1,"signal_handler: Virtual serial port termination completed")
   except:
     log(0,"signal_handler: unable to kill virtual serial port (CAT)")
   
sys.exit(0)
#*-------------------------------------------------------------------------
#* Exception management
#*-------------------------------------------------------------------------
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
#*----------------------------------------------------------------------------
#* SDR Configuration Topology Dictionary
#* Pointers to implemented SDR processors (only USB so far)
#*----------------------------------------------------------------------------
sdr_modes={ 0x00 : ["",""] ,
            0x01 : [cmdRXUSB,cmdTXUSB],
            0x02 : ["",""],
            0x03 : ["",""],
            0x04 : ["",""],
            0x08 : ["",""],
            0x0A : ["",""],
            0x0C : ["",""]}
#*----------------------------------------------------------------------------
#* Transceiver state variables
#*----------------------------------------------------------------------------
fVFOA=14074000
fVFOB= 14074000
vfoAB=0
SPLIT=False
PTT=False
CAT=4800
MODE=0x01
LOCK=False
CLAR=False
LO=14100000
SAMPLE=1200000
TIME_LIMIT=2
WAIT_IDLE=5
DEBUGLEVEL=0

#*----------------------------------------------------------------------------
#* Function definitions
#*----------------------------------------------------------------------------
def log(d,logText):
    if d<=DEBUGLEVEL:
       print("%s:%s %s" % (sys.argv[0],time.ctime(),logText))

#*----------------------------------------------------------------------------
#* getVFO
#* returns either A or B as the current VFO
#*----------------------------------------------------------------------------
def getVFO(v):
    return chr(ord("A")+v)
#*-----------------------------------------------------------------------------$
#* Execute a command and return the result
#*-----------------------------------------------------------------------------$
def execute(cmd):
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (result, error) = p.communicate()

    rc = p.wait()

    if rc != 0:
        log(0,"Error: failed to execute command: %s" % cmd)
        log(0,error)
    return result
#*-----------------------------------------------------------------------
#* printBuffer
#* used to dump content of byte array used for CAT commands and responses
#*-----------------------------------------------------------------------
def printBuffer(rx):
    i=0
    o=''
    while i<len(rx):
      j=rx[i]
      h=hex(j)[2:].zfill(2)
      o=o+' '+h
      i=i+1
    return o
#*--------------------------------------------------------------------------------
#* Identify if the nth bit is set in the x word variable CANDIDATE TO REMOVE
#*--------------------------------------------------------------------------------
#def is_set(x, n):
#    return (x & 2 ** n != 0)

#*---------------------------------------------------------------------------
#* bcdToDec
#* Convert nibble
#*---------------------------------------------------------------------------
def bcdToDec(val):
  return  (val/16*10) + (val%16) 
#*--------------------------------------------------------------------------
#* decToBcd
#* Convert nibble
#*-------------------------------------------------------------------------
def decToBcd(val):
  return  (val/10*16) + (val%10)
#*----------------------------------------------------------------------
#* Decode integer into BCD
#* Frequency is expressed as / 10
#*----------------------------------------------------------------------
def dec2BCD(f):

  fz=int(f/10)
  f0=int(fz/1000000)
  
  x1=fz-f0*1000000
  f1=int(x1/10000)
  
  x2=x1-f1*10000
  f2=int(x2/100)

  x3=x2-f2*100
  f3=int(x3)

  f0=decToBcd(f0)
  f1=decToBcd(f1)
  f2=decToBcd(f2)
  f3=decToBcd(f3)

  log(2,'dec2BCD: f(%d)->f(%d) %x %x %x %x' % (f,fz,f0,f1,f2,f3))
  return bytearray([f0,f1,f2,f3,0x00])
#*-----------------------------------------------------------------------
#* BCD2Dec
#* Convert frequency /10 from BCD to integer
#*-----------------------------------------------------------------------
def BCD2Dec(rxBuffer):
    f=0
    f=f+bcdToDec(rxBuffer[0])*1000000
    f=f+bcdToDec(rxBuffer[1])*10000
    f=f+bcdToDec(rxBuffer[2])*100
    f=f+bcdToDec(rxBuffer[3])*1
    f=f*10
    log(2,'BCD2Dec: Cmd[%s] f=%d' % (printBuffer(rxBuffer),f))
    return f

#*-----------------------------------------------------------------------
#* getFT817
#* Returns FT817's byte descriptor given the mode
#*-----------------------------------------------------------------------
def getFT817mode(m):
    log(2,"getFT817mode: Argument received %d" % m)
    return ft817_modes[m]
#*-------------------------------------------------------------------------
#* Creates a visual clue of the transceiver status (just crude at this point)
#*-------------------------------------------------------------------------
def getStatus():
    if PTT==True:
       sPTT="TX"
    else:
       sPTT="RX"

    sSPLIT=""
    sCLAR=""
    sLOCK=""
    if SPLIT==True:
       sSPLIT="<S>"
    if CLAR==True:
       sCLAR="<+>"
    if LOCK==True:
       sLOCK="<*>"
    return ("(%d/%d) <%s> %s %s %s %s %s" % (fVFOA,fVFOB,getVFO(vfoAB),str(getFT817mode(MODE)).ljust(3," "),str(sPTT).ljust(3," "),str(sSPLIT).ljust(3," "),str(sCLAR).ljust(3," "),str(sLOCK).ljust(3," "))).replace("\n","")


#*------------------------------------------------------------------------
def putStatus():
 log(0,'[Status]->%s' % str(getStatus()).replace("\n",""))
 return


#*=======================================================================
#* Operating actuators
#*=======================================================================

#*-----------------------------------------------------------------------
#* ot_setfreq
#* Set new frequency, kill the receiver process and start a new one
#*-----------------------------------------------------------------------
def ot_setfreq():
    global stime
    killProc(pRX)
    pRX=startReceiver(MODE)
    log(2,'OT[ot_set] VFO(A)=%d VFO(B)=%d' % (fVFOA,fVFOB))
    putStatus()
    return 0
#*-----------------------------------------------------------------------
#* ot_changeVFO
#* Set the receiver to the informed VFO                  ---PENDING---
#*-----------------------------------------------------------------------
def ot_changeVFO():
    log(2,'OT[ot_vfo] VFO (%d/%s)' % (vfoAB,getVFO(vfoAB)))
    putStatus()
    return 0
#*-----------------------------------------------------------------------
#* ot_ptt
#* Change the PTT                                       ---PENDING---
#*-----------------------------------------------------------------------
def ot_ptt():
    log(2,'OT[ot_ptt] PTT change (%s)' % (PTT))
    putStatus()
    return 0
#*-----------------------------------------------------------------------
#* ot_clarify
#* Change the clarify                                       ---PENDING---
#*-----------------------------------------------------------------------
def ot_clarify():
    log(2,'OT[ot_clr] Clafify change (%s)' % (CLAR))
    putStatus()
    return 0
#*-----------------------------------------------------------------------
#* ot_mode
#* Change the clarify                                       ---PENDING---
#*-----------------------------------------------------------------------
def ot_mode():
    log(2,'OT[ot_mod] Mode change (%d/%s)' % (MODE,getFT817mode(MODE)))
    putStatus()
    return 0
#*-----------------------------------------------------------------------
#* ot_lock
#* Implement the lock                                       ---PENDING---
#*-----------------------------------------------------------------------
def ot_lock():
    log(2,'OT[ot_lck] Lock change (%s)' % (LOCK))
    putStatus()
    return 0
#*-----------------------------------------------------------------------
#* ot_readTXmeter
#* Implement the read TX meter                              ---PENDING---
#*-----------------------------------------------------------------------
def ot_readTXmeter():
    log(2,'OT[ot_readTXmeter] Read TX meter change (%s)' % ("Not Implemented"))
    putStatus()
    return 0

#*-----------------------------------------------------------------------
#* ot_setClar
#* Implement the clarifier frequency                         ---PENDING---
#*-----------------------------------------------------------------------
def ot_setClar():
    log(2,'OT[ot_setClar] Set clarifier frequency (%s)' % ("Not Implemented"))
    putStatus()
    return 0
#*=========================[End of actuators]============================

#*=======================================================================
#* processFT817
#* main CAT command processor
#* Command recognition and response format is made here, command operation
#* for implemented commands is set thru the call to a ot_* actuator
#* Not all commands are implemented, not all actuators are more than a stub
#* at this point (work in progress)
#*=======================================================================
def processFT817(rxBuffer,n,s):

#*--- Required definition to avoid re-entrancy problems

    global vfoAB,fVFOA,fVFOB,PTT,CAT,MODE,SPLIT,LOCK

#*--- Process command chain

#*--(0x01=Set Frequency)

    if rxBuffer[4] == 0x01:    #*---- Set Frequency
       fx=bytearray([rxBuffer[0],rxBuffer[1],rxBuffer[2],rxBuffer[3]])
       f=BCD2Dec(fx)
       prevfVFOA=fVFOA
       prevfVFOB=fVFOB
       if vfoAB == 0:
          fVFOA =  int(f)
       else:
          fVFOB =  int(f)
       rc=ot_setfreq()                #*-- Change the execution topology
       r=bytearray([0])
       s.write(r)
       log(1,'CAT[0x01] [%s] VFO[%d/%d] set to VFO[%d/%d]' % (printBuffer(rxBuffer),prevfVFOA,prevfVFOB,fVFOA,fVFOB))
       return  bytearray([0,0,0,0,0]),0

#*--(0x03=Query Frequency)

    if rxBuffer[4] == 0x03:            #*-- Query Frequency
       if vfoAB==0:
          r=dec2BCD(int(fVFOA))
          r[4]=MODE
          s.write(r)
       else:
          r=dec2BCD(int(fVFOB))
          r[4]=MODE
          s.write(r)
       log(2,'CAT[0x03] [%s] VFO[%d/%d] resp[%s]' % (printBuffer(rxBuffer),fVFOA,fVFOB,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0xf7=Read TX status)

    if rxBuffer[4] == 0xf7:           
       STATUS=0x00
       if PTT == False:    #has been inverted
          STATUS = STATUS | 0b10000000
       if SPLIT == True:   #has been inverted 
          STATUS = STATUS | 0b00100000
       r=bytearray([STATUS])
       s.write(r)
       log(2,'CAT[0xf7][%s] PTT(%s) SPL(%s) resp[%s]' % (printBuffer(rxBuffer),not is_set(STATUS,7),is_set(STATUS,5),printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0xE7=Read RX status)

    if rxBuffer[4] == 0xe7:            #*--- RX Status (logging filtered because of high volume
       r=bytearray([0x00])
       s.write(r)
       log(2,'CAT[0xE7][%s] resp[%s]' % (printBuffer(rxBuffer),printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0xBB=Read EEPROM *Response falsified*)

    if rxBuffer[4] == 0xBB:            #*--- Read EEPROM
       r=bytearray([0,0])
       s.write(r)
       log(2,'CAT[0xBB] *TEMP* [%s] resp[%s]' % (printBuffer(rxBuffer),printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0x81=Switch VFO A/B)

    if rxBuffer[4] == 0x81:            #*--- Switch VFO A/B
       prevAB=vfoAB
       if vfoAB == 0:
          vfoAB = 1
       else:
          vfoAB = 0
       rc=ot_changeVFO()               #*--- change topology
       r=bytearray([0x00])
       s.write(r)
       log(1,'CAT[0x81] [%s] VFO(%s->%s) resp[%s]' % (printBuffer(rxBuffer),getVFO(prevAB),getVFO(vfoAB),printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0x00=Lock status)

    if rxBuffer[4] == 0x00:            #*--- Lock status
       prevLOCK=LOCK
       if LOCK==True:
          r=bytearray([0xf0])
          LOCK=False
       else:
          r=bytearray([0])
          LOCK=True
       rc=ot_lock() 
       s.write(r)
       log(1,'CAT[0x00] [%s] LOCK(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevLOCK,LOCK,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0x02=SPLIT on)

    if rxBuffer[4] == 0x02:            #*--- Split On
       prevSPLIT=SPLIT
       if SPLIT==True:
          r=bytearray([0xf0])
          SPLIT=False
       else:
          r=bytearray([0x00])
          SPLIT=True
       rc=ot_split()
       s.write(r)
       log(1,'CAT[0x02] [%s] SPLIT(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevSPLIT,SPLIT,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0x07=Set MODE)

    if rxBuffer[4] == 0x07:            #*--- Set operating mode
       prevMODE=MODE
       prevs=getFT817mode(MODE)
       nextMODE=rxBuffer[0]
       s=getFT817mode(nextMODE)
       if s==None:
          log(0,'CAT[0x07] Invalid CAT Mode(%d), ignore' % (nextMODE))
          r=bytearray([0x00])
       else:   
          MODE=nextMODE
          r=bytearray([0x00])
       rc=ot_mode()
       s.write(r)
       log(1,'CAT[0x07] [%s] MODE(%d<%s>->%d<%s>)  resp[%s]' % (printBuffer(rxBuffer),prevMODE,prevs,MODE,s,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*-- (0x08=PTT ON place transceiver in transmit mode)

    if rxBuffer[4] == 0x08:            #*--- PTT ON and Status
       prevPTT=PTT
       PTT=True
       if prevPTT==True:
          r=bytearray([0xf0])
       else:
          r=bytearray([0x00])
       rc=ot_ptt()
       s.write(r)
       log(1,'CAT[0x08] [%s] PTT(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevPTT,PTT,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*-- Commands not implemented (and unlikely to be used in HF)
#*--(0x09=Set Repeater offset direction)
#*--(0x0A=Set DCS/CTCSS mode)
#*--(0x0B=Set CTCSS Tone Frequency)
#*--(0x0C=Set DCS Code)
#*--(0x0F=Turn on FT817)

    if ((rxBuffer[4] == 0x09) or (rxBuffer[4] == 0x0A) or (rxBuffer[4] == 0x0B) or (rxBuffer[4]==0x0C) or (rxBuffer[4]==0x0F)):   
       log(0,'CAT[NI] [%s] ignored' % (printBuffer(rxBuffer)))
       return  bytearray([0,0,0,0,0]),0

#--(0x10=Read TX keyed state (undocumented command) source:http://www.ka7oei.com/ft817_meow.html

    if rxBuffer[4] == 0x10:            #*--- Read TX Keyed state (undoc)
       if PTT==True:
          r=bytearray([0xf0])
       else:
          r=bytearray([0x00])
       s.write(r)
       log(1,'CAT[0x10] [%s] PTT(%s)  resp[%s]' % (printBuffer(rxBuffer),PTT,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#--(0x82=SPLIT off)

    if rxBuffer[4] == 0x82:            #*--- Split Off
       prevSPLIT=SPLIT
       if SPLIT==True:
          r=bytearray([0x00])
       else:
          r=bytearray([0xf0])
       SPLIT=False
       rc=ot_split()
       s.write(r)
       log(1,'CAT[0x82] [%s] SPLIT(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevSPLIT,SPLIT,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0x85=Clarifier off)

    if rxBuffer[4] == 0x85:            #*--- Clarifier Off
       prevCLAR=CLAR
       if CLAR==True:
          r=bytearray([0x00])
       else:
          r=bytearray([0xf0])
       CLAR=False
       rc=ot_clarify()
       s.write(r)
       log(1,'CAT[0x85] [%s] CLARIFIER(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevCLAR,CLAR,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0x05=Clarifier On)

    if rxBuffer[4] == 0x05:            #*--- Clarifier On
       prevCLAR=CLAR
       if CLAR==True:
          r=bytearray([0xf0])
       else:
          r=bytearray([0x00])
       CLAR=True
       rc=ot_clarify()
       s.write(r)
       log(1,'CAT[0x05] [%s] CLARIFIER(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevCLAR,CLAR,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0x80=Lock off)

    if rxBuffer[4] == 0x80:            #*--- Lock off
       prevLOCK=LOCK
       if LOCK==True:
          r=bytearray([0x00])
       else:
          r=bytearray([0xf0])
       LOCK=False
       rc=ot_lock()
       s.write(r)
       log(1,'CAT[0x80] [%s] LOCK(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevLOCK,LOCK,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0x88=PTT Off and give status)

    if rxBuffer[4] == 0x88:            #*--- PTT OFF and Status
       prevPTT=PTT
       PTT=False
       if prevPTT==True:
          r=bytearray([0x00])
       else:
          r=bytearray([0xf0])
       rc=ot_ptt()
       s.write(r)
       log(1,'CAT[0x88] [%s] PTT(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevPTT,PTT,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--Commands ignored
#*--(0x8f=Turn off FT817)
#*--(0xBA=Unknown status, not documented)
#*--(0xBC=Write EEPROM, there is no EEPROM here, just ignore it)
    if ((rxBuffer[4] == 0x8f) or (rxBuffer[4] == 0xa7) or (rxBuffer[4] == 0xba) or (rxBuffer[4]==0xbc)):            #*--- Lock status
       log(0,'CAT[N*] [%s] ignored' % (printBuffer(rxBuffer)))
       return  bytearray([0,0,0,0,0]),0

#*--(0xBD=Reads TX Metering , NOT IMPLEMENTED YET)

    if rxBuffer[4] == 0xBD:            #*--- YET TO BE IMPLEMENTED
       rc=ot_readTXmeter()
       log(0,'CAT[0xBD] [%s] PTT(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevPTT,PTT,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*--(0xF5=Set clarifier frequency , NOT IMPLEMENTED YET)

    if rxBuffer[4] == 0xF5:            #*--- YET TO BE IMPLEMENTED
       rc=ot_setClar()
       log(0,'CAT[0xF5] [%s] PTT(%s->%s)  resp[%s]' % (printBuffer(rxBuffer),prevPTT,PTT,printBuffer(r)))
       return  bytearray([0,0,0,0,0]),0

#*-- Commands ignored
#*--(0xF9=Set Repeater Offset Amount
#*--(0xBE=Reset FT817 to factory defaults, unable to

    if ((rxBuffer[4] == 0xbe) or (rxBuffer[4] == 0xf9)):
       log(0,'CAT[N-] [%s] ignored' % (printBuffer(rxBuffer)))
       return  bytearray([0,0,0,0,0]),0
#*===================================================================================================
#*----------------------------------------------------------------------------
#* SDR Capabilities
#*----------------------------------------------------------------------------
def isSDRCapable(m,t):

    if ((sdr_modes[m][0] != "") and (t==0)):
       return True
    if (sdr_modes[m][1] != "") and (t==1):
       return True
    return False

#*----------------------------------------------------------------------------
#* Boot SDR Processor
#*----------------------------------------------------------------------------
def bootSDR():
    for key in sdr_modes:
        SDRmodeStr=getFT817mode(SDRmode).ljust(4," ")
        log(0,"SDR Capabilities: Mode <%s> RX(%s) TX(%s)" % (SDRmodeStr.ljust(3," "),str(isSDRCapable(key,0)).ljust(5," "),str(isSDRCapable(key,1)).ljust(5," ")))

#*--------------------------------------------------------------------------------
#* startSDR
#* Start SDR processor with macro expansion, wait for success key to happen
#*--------------------------------------------------------------------------------
def startSDR(cmd,stOK):

    cmd=cmd.replace("%LO%",str(LO))
    cmd=cmd.replace("%FREQ%",str(fVFOA))
    cmd=cmd.replace("%SAMPLE%",str(SAMPLE))
    log(1,"startSDR:%s" % cmd)
    p=startProc(cmd,stOK)
    if p==None:
       log(0,"startSDR: Process launch failed")
    return p

#def stopSDR(p):
#    killProc(p)
#    return 0

#*----------------------------------------------------------------------------------
#* startReceiver
#* manages the capability and launch receiver
#*----------------------------------------------------------------------------------
def startReceiver(m):
    global PTT
    
    PTT=False
    log(1,"startReceiver: Starting front-end processor")
    s=sdr_modes[m][0]
    if s=="":
       log(0,"Front-End SDR processor not found, QUITTING!")
       exit()
    p=startSDR(s,"Reading samples in async mode..")
    log(1,"startReceiver: Starting listener process")
    return p
#*----------------------------------------------------------------------------------
#* killReceiver
#* kill receiver process and nullify it
#*----------------------------------------------------------------------------------
def killReceiver():
     global pRX
     log(1,"killReceiver: Starting front-end processor")
     killProc(pRX)
     pRX=None
     return

#*----------------------------------------------------------------------------
#* MAIN PROGRAM
#*----------------------------------------------------------------------------
try:
 log(0,"Booting transceiver PID(%d)" % (os.getPid()))

#*----------------------------------------------------------------------------
#* Process arguments
#*----------------------------------------------------------------------------
 p = argparse.ArgumentParser()
 p.add_argument('-i', help="Input serial port",default='/tmp/ttyv0')
 p.add_argument('-o', help="Input serial port",default='/tmp/ttyv1')
 p.add_argument('-r', help="Serial port rate",default=4800)
 p.add_argument('-v', help="Verbose",default=0)
 p.add_argument('-m', help="Mode",default=1)
 p.add_argument('-l', help="Lock",action="store_true",default=False)
 p.add_argument('-f', help="Frequency",default=14074000)
 p.add_argument('-c', help="Clarifier",action="store_true",default=False)
 p.add_argument('-s', help="Split",action="store_true",default=False)

#*----------------------------------------------------------------------------
#* Establish initial values based on arguments
#*----------------------------------------------------------------------------

 args = p.parse_args()
 fVFOA=int(args.f)
 fVFOB=int(args.f)
 MODE=args.m
 LOCK=args.l
 SPLIT=args.s
 CLAR=args.c
 DEBUGLEVEL=args.v
 SDRmode=0x01

#*-----------------------------------------------------------------------------
#* Special feature, force removal of all involved processes
#*-----------------------------------------------------------------------------

# if args.k == True:
#    log(0,"main: removing previous processes")
#    killProcList('rtl_sdr')
#    killProcList('socat')

#*----------------------------------------------------------------------------
#* Start virtual serial port pair to communicate CAT commands
#*----------------------------------------------------------------------------
 z=startProc('socat -d -d pty,raw,echo=0,link=/tmp/ttyv0 pty,raw,echo=0,link=/tmp/ttyv1',"starting data transfer loop")
 log(0,"main: started virtual serial port %s" %  psutil.Process(z.pid))
 time.sleep(1)
#*----------------------------------------------------------------------------
#* Boot SDR processor and perform initialization
#*---------------------------------------------------------------------------- 
 rc=bootSDR()

 s=serial.Serial(args.o,args.r)
 log(0,'Client[%s]==>(%s) (%d)' % (args.o,args.i,args.r))

 CAT=args.r
 vfoAB=0

#*----------------------------------------------------------------------------
#* Start SDR processor, always starts with the receiver of the mode
#*----------------------------------------------------------------------------
 pRX=startReceiver(MODE)

#*----------------------------------------------------------------------------
#* Main receiving loop
#*----------------------------------------------------------------------------
 o=''
 n=0
 vfoAB=0
 rxBuffer=bytearray([0,0,0,0,0])
 putStatus()
#*----------------------------------------------------------------------------
#* Infinite loop
#*----------------------------------------------------------------------------
 while True :

#*------ Process CAT commands block if none (to improve performance avoiding polling)
#  if ( (s.inWaiting()>0) ):

     c = s.read()
     for d in c:
         i = ord(d)
         rxBuffer[n]=i
         h = hex(i)[2:]
         o = o + ' ' + h
         n=n+1
         if n==5:
            (rxBuffer,n)=processFT817(rxBuffer,n,s)

#*===========================[infinite loop]====================================

#*------- Process output from receiver subordinate processes
#
#  if 'pRX1' in globals() :
#     rst=non_block_read(pRX1.stdout)
#     if rst!="":
#        log(0,rst.replace("\n",""))
#
#  if 'pRX2' in globals() :
#     rst=non_block_read(pRX2.stdout)
#     if rst!="" and rst.find("(unknown)")== -1:
#        log(0,rst.replace("\n",""))
#
#         
#  if 'pTX' in globals() and args.v==True:
#     rst=non_block_read(pTX.stdout)
#     if args.v == True and rst!="":
#        log(0,rst.replace("\n",""))


 exit()        
#********************************************************************************
#*                                Exception handler
#********************************************************************************
except(SyntaxError):
 log(0,"Transceiver abnormal syntax error detected, clean up completed!")
 if pRX != None:
    killProc(pRX)
 if pTX != None:
    killProc(pTX)
 if z != None:
    killProc(z)
 sys.exit(0)

