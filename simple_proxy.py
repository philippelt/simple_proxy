#!/usr/bin/python3

import os, sys, socket, textwrap, threading, zlib, ssl, gzip
from datetime import datetime


BUFLEN = 65535
TIMEOUT = 60
PROXY_TRACE = os.getenv("PROXY_TRACE") is not None


def debugTrace(*args):
    if PROXY_TRACE :
        print(*args)



class SimpleProxy:


    def __init__(self, localHostPort, targetHostPort, sslTarget):

        print("Listening on %s -> relaying to %s [http%s]" % (localHostPort, targetHostPort, "s" if sslTarget else ""))
        self.localHostPort = localHostPort.encode("utf-8")
        self.targetHostPort = targetHostPort.encode("utf-8")
        self.sslTarget = sslTarget

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
            
            threading.Thread(target=ProxyHandler, args=(self.client, self.printLock, self.localHostPort, self.targetHostPort, self.sslTarget)).start()



class ProxyHandler:


    def __init__(self, client, printLock, localHostPort, targetHostPort, sslTarget):

        self.client = client
        self.localHostPort = localHostPort
        self.targetHostPort = targetHostPort
        self.printLock = printLock
        self.sslTarget = sslTarget

        self.targetConnect()
        # Read/Write until close
        while True:

            # Read from client and send to target
            if not self.readHttp(self.client) : break
            if self.httpBody and self.encoding == "gzip" : self.unGzipBody()
            if self.sslTarget : self.substituteSchema()
            self.substituteHostName()
            self.dumpHttp('>>> SENT')
            self.original = self.httpCommand
            if self.httpBody and self.encoding == "gzip" : self.gzipBody()
            if self.contentLength and self.contentLength != len(self.httpBody) : self.updateContentLengthHeader()
            self.writeHttp(self.target)

            # Read response from target and send back to client
            self.readHttp(self.target)
            if self.httpBody and self.encoding == "gzip" : self.unGzipBody()
            self.dumpHttp('<<< RECEIVED')
            if self.sslTarget : self.substituteSchema(forward=False)
            self.substituteHostName(forward=False)
            if self.httpBody and self.encoding == "gzip" : self.gzipBody()
            if self.contentLength and self.contentLength != len(self.httpBody) : self.updateContentLengthHeader()
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
        self.encoding = None
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

            elif self.contentLength == 0 or (self.contentLength and len(inBuffer)+overHead >= self.contentLength) :
                self.httpBody = inBuffer
                return True


    def assembleChunks(self, inBuffer):
        debugTrace("assembleChunk")

        outBuffer = b''
        while True:

            try:
                i = inBuffer.find(b"\r\n")
                if i == -1: return outBuffer
                size = int(inBuffer[:i], 16)
                if size == 0 : return outBuffer
                inBuffer = inBuffer[i+2:]
                
                outBuffer += inBuffer[:size]
                inBuffer = inBuffer[size+2:]
            except:
                import pdb
                pdb.set_trace()


    def targetConnect(self):
        debugTrace("targetConnect")

        if b";" in self.targetHostPort :
            targetHost, targetPort = targetHostPort.split(b":")
            targetPort = int(targetPort)
        else:
            targetHost, targetPort = targetHostPort, (80 if not self.sslTarget else 443)

        (soc_family, _, _, _, address) = socket.getaddrinfo(targetHost, targetPort)[0]
        soc = socket.socket(soc_family)
        soc.connect(address)
        soc.settimeout(TIMEOUT)

        if self.sslTarget :
            self.target = ssl.wrap_socket(soc)
        else :
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

        def condReplace(s, lF, rB):
            start = 0
            shift = len(lF)
            i = s.find(lF, start)
            while i != -1 :
                start = i+1
                if s[i-1] != 46 :
                    s = s[:i] + rB + s[i+shift:]
                i = s.find(lF, start)
            return s

        if forward :
            lookFor, replaceBy = self.localHostPort, self.targetHostPort
        else:
            lookFor, replaceBy = self.targetHostPort, self.localHostPort
            
        self.httpHeader = condReplace(self.httpHeader, lookFor, replaceBy)
        if self.httpBody : self.httpBody = condReplace(self.httpBody, lookFor, replaceBy)


    def substituteSchema(self, forward=True):
        debugTrace("substituteSchema")

        if forward :
            lookFor, replaceBy = b"http://" + self.localHostPort, b"https://" + self.targetHostPort
        else :
            lookFor, replaceBy = b"https://" + self.targetHostPort, b"http://" + self.localHostPort

        self.httpHeader = self.httpHeader.replace(lookFor, replaceBy)
        if self.httpBody : self.httpBody = self.httpBody.replace(lookFor, replaceBy)


    def writeHttp(self, soc):
        debugTrace("writeHttp")

        outBuffer = b" ".join(self.httpCommand) + b"\r\n" + self.httpHeader + b"\r\n\r\n"
        if self.httpBody : outBuffer += self.httpBody
        soc.send(outBuffer)


    def unGzipBody(self) :
        debugTrace("unGzipBody")

        if self.chunk :
            self.httpBody = gzip.decompress(self.assembleChunks(self.httpBody))
        else:
            self.httpBody = gzip.decompress(self.httpBody)


    def gzipBody(self):
        debugTrace("gzipBody")

        body = gzip.compress(self.httpBody)
        if self.chunk :
            hexSize = hex(len(body))[2:].encode("ascii")
            self.httpBody = hexSize + b"\r\n" + body
        else:
            self.httpBody = body 


    def updateContentLengthHeader(self):

        self.contentLength = len(self.httpBody)
        headers = self.httpHeader.splitlines()
        for h in list(headers) :
            if h.lower().startswith(b"Content-Length") :
                headers.remove(h)
                headers.add(b"Content-Length: "+str(self.contentLength).encode("ascii"))
                break
        self.httpHeader = b"\r\n".join(headers)


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

                if "text" not in self.contentType and "xml" not in self.contentType and "urlencod" not in self.contentType :
                    print("\nBinary body content, size = ", self.contentLength)
                
                elif self.contentLength > 40000 or len(self.httpBody) > 40000 :
                    print("\nLarge body skipped, size = ", self.contentLength if self.contentLength else len(self.httpBody))

                elif self.encoding not in ["deflate"] :
                    encoding = self.encoding
                    if encoding == "gzip" :
                        try:
                            encoding = contentType.split(";")[1].split("=")[1]
                        except:
                            encoding = "latin1"
                    for l in self.httpBody.decode(encoding or "latin1").splitlines() :
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
    sslTarget = os.getenv("PROXY_SSL") is not None

    proxy = SimpleProxy(localHostPort, targetHostPort, sslTarget)
    proxy.run()
    
