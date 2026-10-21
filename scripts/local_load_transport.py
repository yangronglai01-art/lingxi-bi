"""Persistent per-thread loopback HTTP client, expiring idle sockets before send."""
import http.client
import json
import threading
import time
from urllib.parse import urlsplit

class LocalLoadClient:
    def __init__(self,url,timeout=15,idle_seconds=4):
        address=urlsplit(url)
        if address.scheme!='http' or address.hostname not in {'127.0.0.1','localhost'}:
            raise ValueError('Load client requires loopback HTTP')
        self.host=address.hostname;self.port=address.port
        self.timeout=timeout;self.idle_seconds=idle_seconds
        self.local=threading.local();self.lock=threading.Lock()
        self.connections=[];self.idle_renewals=0;self.closed=False

    def request(self,path,headers,body=None):
        if self.closed:raise RuntimeError('Client already closed')
        started=time.monotonic();phase='connect'
        connection=getattr(self.local,'connection',None)
        if connection is not None and started-getattr(self.local,'last_used',started)>=self.idle_seconds:
            connection.close();connection=None;self.local.connection=None
            with self.lock:self.idle_renewals+=1
        try:
            if connection is None:
                connection=http.client.HTTPConnection(self.host,self.port,timeout=self.timeout)
                with self.lock:self.connections.append(connection)
                connection.connect();self.local.connection=connection
            phase='send'
            connection.request('POST' if body is not None else 'GET',path,
                body=json.dumps(body).encode() if body is not None else None,headers=headers)
            phase='response_headers';response=connection.getresponse()
            phase='response_body';raw=response.read()
            phase='decode';value=json.loads(raw)
            self.local.last_used=time.monotonic()
            return value,response.status,time.monotonic()-started,{k.lower():v for k,v in response.headers.items()}
        except Exception as exc:
            if connection:connection.close()
            self.local.connection=None
            raise RuntimeError('HTTP phase='+phase+' path='+path+' error='+type(exc).__name__) from exc

    def close(self):
        self.closed=True
        for connection in self.connections:connection.close()
