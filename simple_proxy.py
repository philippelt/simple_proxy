#!/usr/bin/python3
# Author : philippelt@sourceforge.net

import os, sys, socket, textwrap, threading, zlib
from datetime import datetime


BUFLEN = 65535
TIMEOUT = 60
PROXY_TRACE = os.getenv("PROXY_TRACE") is not None


def debugTrace(*args):
    if PROXY_TRACE :
        print(*args)



class SimpleProxy:


    def __init__(self, localHostPort, targetHostPort):

        print("Listening on %s -> relaying to %s" % (localHostPort, targetHostPort))
        self.localHostPort = localHostPort.encode("utf-8")
        self.targetHostPort = targetHostPort.encode("utf-8")

        # Bind to listening socket
        soc = socket.socket(socket.AF_INET)
        soc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if b":" in self.localHostPort :
            localHost, localPort = self.localHostPort.split(b":")
            localPort = int(localPort)
        else:
            localHost, localPort = self.localHostPort, 80
        soc.bind((localHost, localPort))

        # Client = originator, target = destination server
        self.proxy = soc
        self.printLock = threading.Lock()

    
    def run(self):

        while True:
            # Wait for connection until proxy killed
            self.proxy.listen(0)
            self.client, self.remoteInfo = self.proxy.accept()
            self.client.settimeout(TIMEOUT)
            debugTrace("Client request received and accepted")
            
            threading.Thread(target=ProxyHandler, args=(self.client, self.printLock, self.localHostPort, self.targetHostPort)).start()



class ProxyHandler:


    def __init__(self, client, printLock, localHostPort, targetHostPort):

        self.client = client
        self.localHostPort = localHostPort
        self.targetHostPort = targetHostPort
        self.printLock = printLock

        self.targetConnect()
        # Read/Write until close
        while True:
            if not self.readHttp(self.client) : break
            self.substituteHostName()
            self.dumpHttp('>>> SENT')
            self.original = self.httpCommand
            self.writeHttp(self.target)
            self.readHttp(self.target)
            self.dumpHttp('<<< RECEIVED')
            self.substituteHostName(forward=False)
            self.writeHttp(self.client)


    def readHttp(self, soc):
        debugTrace("readHttp")

        inBuffer = b''
        chunkBuffer = b''
        overHead = 0
        self.httpCommand = None
        self.httpHeader = None
        self.headerProcessed = False
        self.contentLength = None
        self.chunk = False

        while True:

            try:
                inBuffer += soc.recv(BUFLEN)
                if len(inBuffer) == 0 : return False
                debugTrace("Read %i bytes" % len(inBuffer))
            except KeyboardInterrupt :
                print(len(inBuffer))
                print(inBuffer)
                sys.exit(1)
            except socket.timeout :
                return False

            if not self.httpCommand :
                i = inBuffer.find(b"\r\n")
                if i != -1 :
                    self.httpCommand = inBuffer[:i].split()
                    try:
                        inBuffer = inBuffer[i+2:]
                    except:
                        inBuffer = b''
                    overHead = i+2
            
            if not self.httpHeader :
                i = inBuffer.find(b"\r\n\r\n")
                if i != -1 :
                    self.httpHeader = inBuffer[:i]
                    try:
                        inBuffer = inBuffer[i+4:]
                    except:
                        inBuffer = b''
                    overHead += i+4
           
            if self.httpHeader and not self.headerProcessed:
                self.contentType = self.lookForHeaderValue(b"Content-Type")
                self.contentLength = self.lookForHeaderValue(b"Content-Length") or 0
                if self.contentLength :
                    self.contentLength = int(self.contentLength)
                    debugTrace("Expected content length = ", self.contentLength)
                self.encoding = self.lookForHeaderValue(b"Content-Encoding")
                if self.lookForHeaderValue(b"Transfer-Encoding") :
                    self.chunk = self.lookForHeaderValue(b"Transfer-Encoding") == "chunked"
                self.headerProcessed = True

            if self.chunk :
                debugTrace("Chunk received")
                if inBuffer.endswith(b"\r\n0\r\n\r\n") :
                    self.httpBody = inBuffer
                    return True

            elif self.contentLength == 0 or len(inBuffer)+overHead >= self.contentLength :
                self.httpBody = inBuffer
                return True


    def assembleChunks(self, inBuffer):

        outBuffer = b''
        while True:

            i = inBuffer.find(b"\r\n")
            if i == -1: return outBuffer
            size = int(inBuffer[:i], 16)
            if size == 0 : return outBuffer
            inBuffer = inBuffer[i+2:]
            
            outBuffer += inBuffer[:size]
            inBuffer = inBuffer[size+2:]


    def targetConnect(self):
        debugTrace("targetConnect")

        if b";" in self.targetHostPort :
            targetHost, targetPort = targetHostPort.split(b":")
            targetPort = int(targetPort)
        else:
            targetHost, targetPort = targetHostPort, 80
        (soc_family, _, _, _, address) = socket.getaddrinfo(targetHost, targetPort)[0]
        soc = socket.socket(soc_family)
        soc.connect(address)
        soc.settimeout(TIMEOUT)
        self.target = soc


    def lookForHeaderValue(self, header):
        debugTrace("lookForHeaderValue ", header)

        headers = self.httpHeader.splitlines()
        for h in headers :
            i = h.find(b":")
            name, value = h[:i], h[i+1:]
            if name.upper() == header.upper().strip() :
                return value.strip().decode("utf-8")
        return None


    def substituteHostName(self, forward=True):
        debugTrace("substituteHostName ", forward)

        if forward :
            lookFor, replaceBy = self.localHostPort, self.targetHostPort
        else:
            lookFor, replaceBy = self.targetHostPort, self.localHostPort
            
        self.httpHeader = self.httpHeader.replace(lookFor, replaceBy)
        self.httpBody = self.httpBody.replace(lookFor, replaceBy)


    def writeHttp(self, soc):
        debugTrace("writeHttp")

        outBuffer = b" ".join(self.httpCommand) + b"\r\n" + self.httpHeader + b"\r\n\r\n"
        if self.httpBody : outBuffer += self.httpBody
        soc.send(outBuffer)


    def dumpHttp(self, direction):

        try:
            self.printLock.acquire()
            print("\n"+"*"*120)
        
            print('%-15s %s : %s\n' % (direction, datetime.isoformat(datetime.now()), (b" ".join(self.httpCommand)).decode("utf-8")))

            if direction[0] == '<' :
                print(" "*44, "Response to : ", (b" ".join(self.original)).decode("utf-8"))
            print()

            print('Headers:')
            for l in self.httpHeader.splitlines():
                i = l.find(b":")
                print('\t%25s : %s' % (l[:i].decode("utf-8"), l[i+1:].decode("utf-8")))

            if self.httpBody :

                print('\nBody:')
               
                if self.encoding == "gzip" :
                    body = zlib.decompress(self.assembleChunks(self.httpBody), 16+zlib.MAX_WBITS)
                else:
                    body = self.httpBody

                if "text" not in self.contentType and "xml" not in self.contentType and "urlencod" not in self.contentType :
                    print("\nBinary body content, size = ", self.contentLength)
                
                elif self.contentLength > 4000 :
                    print("\nLarge body skipped, size = ", self.contentLength)

                elif self.encoding not in ["deflate"] :
                    if self.encoding == "gzip" : self.encoding = self.contentType.split(";")[1].split("=")[1]
                    for l in body.decode(self.encoding or "latin1").splitlines() :
                        for ll in textwrap.wrap(l, width=120):
                            print('\t', ll)

                else :
                    print("Body non printable")
        
        except Exception as e:
            print("dumpHttp Exception : ", e)

        finally:
            self.printLock.release()
   


if __name__ == '__main__':

    # Get required parameters, only PROXY_TARGET_HOST is mandatory
    localHostPort  = os.getenv("PROXY_LOCAL") or "localhost:8880"
    targetHostPort = os.getenv("PROXY_TARGET")

    proxy = SimpleProxy(localHostPort, targetHostPort)
    proxy.run()
    
