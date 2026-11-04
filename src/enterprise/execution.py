"""Restricted StarRocks execution with bounded streaming and physical cancellation."""
import datetime
import decimal
import json
import os
import threading
import time
import math
import pymysql
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
from .settings import name


def wire(value):
    if isinstance(value,decimal.Decimal):return format(value,'f')
    if isinstance(value,(datetime.date,datetime.datetime)):return value.isoformat()
    if isinstance(value,bytes):raise ValueError('Binary result columns are not supported')
    if isinstance(value,float) and not __import__('math').isfinite(value):raise ValueError('Nonfinite result')
    return value


def bind(sql,mapping):
    tree=sqlglot.parse_one(sql,read='starrocks')
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source,exp.Table):
                logical=source.name.lower()
                if source.db or source.catalog or logical not in mapping:raise PermissionError('Unapproved physical source')
                if not source.alias:source.set('alias',exp.TableAlias(this=exp.to_identifier(logical)))
                source.set('this',exp.to_identifier(name(mapping[logical]),quoted=True))
    return tree.sql(dialect='starrocks')


class Executor:
    def __init__(self,settings):self.settings=settings

    def connect(self,scope,stream=False):
        c=self.settings.scopes[scope]
        password=os.environ.get(c['password_env'])
        if not password:raise RuntimeError('Restricted StarRocks credential is missing')
        tls={'ca':c['ssl_ca'],'check_hostname':True} if c.get('ssl_ca') else None
        return pymysql.connect(host=c['host'],port=int(c.get('port',9030)),user=c['user'],password=password,
            database=c['database'],autocommit=True,charset='utf8mb4',connect_timeout=5,
            read_timeout=self.settings.query_seconds+5,write_timeout=5,ssl=tls,
            cursorclass=pymysql.cursors.SSCursor if stream else pymysql.cursors.Cursor)

    def run(self,scope,sql,alive=lambda:True,on_connection=lambda _:None,*,deadline=None):
        begin=time.monotonic()
        deadline=min(deadline,begin+self.settings.query_seconds) if deadline is not None else begin+self.settings.query_seconds
        if deadline<=begin:raise TimeoutError('SQL_DEADLINE')
        done=threading.Event();stop=[];connection=self.connect(scope,True)
        connection_id=connection.thread_id()
        def monitor():
            while not done.wait(.2):
                try:reason='SQL_DEADLINE' if time.monotonic()>=deadline else ('QUERY_CANCELLED' if not alive() else None)
                except Exception:reason='AUTHORITY_UNAVAILABLE'
                if not reason:continue
                stop.append(reason)
                try:
                    with self.connect(scope) as killer:
                        with killer.cursor() as cursor:cursor.execute('KILL QUERY '+str(int(connection_id)))
                except Exception:stop.append('CANCEL_UNCONFIRMED')
                return
        watcher=threading.Thread(target=monitor,daemon=True);watcher.start()
        try:
            on_connection(connection_id)
            with connection.cursor() as cursor:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('SQL_DEADLINE')
                cursor.execute('SET query_timeout = '+str(max(1,math.ceil(remaining))))
                cursor.execute('SET query_mem_limit = '+str(self.settings.query_memory_bytes))
                cursor.execute('SET pipeline_dop = 1')
                if time.monotonic()>=deadline:raise TimeoutError('SQL_DEADLINE')
                if not alive() or stop:raise TimeoutError('QUERY_CANCELLED')
                cursor.execute(sql)
                columns=[d[0] for d in cursor.description];types=[d[1] for d in cursor.description]
                rows=[];size=0;truncated=False
                for row in cursor:
                    if stop:raise TimeoutError(stop[0])
                    converted=[wire(v) for v in row]
                    size+=len(json.dumps(converted,ensure_ascii=False).encode())
                    if len(rows)>=self.settings.max_rows or size>self.settings.max_bytes:
                        truncated=True
                        # PyMySQL SSCursor.close normally drains unread rows. The pinned
                        # driver has no public abort API: detach before closing the socket.
                        cursor._result.unbuffered_active=False
                        connection.close()
                        break
                    rows.append(converted)
                query_id=None
                if not truncated:
                    cursor.execute('SELECT last_query_id()');query_id=str(cursor.fetchone()[0])
                if stop:raise TimeoutError(stop[0])
                return dict(columns=columns,rows=rows,row_count=len(rows),column_types=types,
                    truncated=truncated,query_id=query_id,connection_id=connection_id,
                    elapsed_seconds=round(time.monotonic()-begin,4),decimal_encoding='fixed-point-string')
        except pymysql.MySQLError:
            if stop:raise TimeoutError(stop[0]) from None
            raise
        finally:
            done.set();watcher.join(timeout=7)
            if connection.open:connection.close()
