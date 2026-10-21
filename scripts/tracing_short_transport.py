"""Original urllib short-connection path with phase evidence and no retries."""
import http.client
import json
import threading
import time
import uuid
from urllib.request import Request,HTTPHandler,build_opener
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit

class TracingUrllibClient:
    def __init__(self,url,timeout=15):
        parsed=urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost'}:
            raise ValueError('Only loopback HTTP is allowed')
        self.url=url;self.timeout=timeout;self.local=threading.local();self.lock=threading.Lock()
        self.connections=[];self.failures=[];self.idle_renewals=0;self.closed=False
        owner=self
        class Connection(http.client.HTTPConnection):
            def connect(self):
                owner.local.trace['phase']='connect'
                with owner.lock:owner.connections.append(self)
                super().connect()
            def send(self,data):
                if self.sock is None:self.connect()
                owner.local.trace['phase']='send'
                super().send(data)
            def getresponse(self):
                owner.local.trace['phase']='response_headers'
                return super().getresponse()
        class Handler(HTTPHandler):
            def http_open(self,req):return self.do_open(Connection,req)
        # Retain urllib's normal proxy/bypass behavior; never print proxy secrets.
        self.opener=build_opener(Handler())

    def request(self,path,headers,body=None):
        if self.closed:raise RuntimeError('Client is closed')
        start=time.monotonic()
        trace=dict(id=uuid.uuid4().hex,method='POST' if body is not None else 'GET',path=path,phase='open')
        self.local.trace=trace
        req=Request(self.url+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            try:response=self.opener.open(req,timeout=self.timeout)
            except HTTPError as exc:response=exc
            with response:
                trace['phase']='response_body';raw=response.read()
                trace['phase']='decode';value=json.loads(raw)
                return value,response.status,time.monotonic()-start,{k.lower():v for k,v in response.headers.items()}
        except Exception as exc:
            underlying=exc.reason if isinstance(exc,URLError) else exc
            trace.update(elapsed_seconds=round(time.monotonic()-start,4),error=type(exc).__name__,
                underlying_error=type(underlying).__name__,errno=getattr(underlying,'errno',None),
                winerror=getattr(underlying,'winerror',None))
            with self.lock:self.failures.append(dict(trace))
            raise RuntimeError('HTTP phase='+trace['phase']+' path='+path+' error='+type(underlying).__name__) from exc

    def close(self):
        self.closed=True
        for connection in self.connections:connection.close()
