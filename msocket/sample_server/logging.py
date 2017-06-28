# -*- coding:utf8 -*-
from __future__ import absolute_import

import socket
import struct
import cPickle
import logging
import Queue
import threading
from logging.config import RESET_ERROR, dictConfig, fileConfig

from ..compat import socketserver, address_type
from msocket import server

LOG_SERVERS = {}
LOG_CONFIG_SERVERS = {}


class LoggingConfigRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        """
        Handle a request.

        Each request is expected to be a 4-byte length, packed using
        struct.pack(">L", n), followed by the config file.
        Uses fileConfig() to do the grunt work.
        """
        try:
            conn = self.request
            chunk = conn.recv(4)
            if len(chunk) == 4:
                slen = struct.unpack(">L", chunk)[0]
                chunk = self.connection.recv(slen)
                while len(chunk) < slen:
                    chunk = chunk + conn.recv(slen - len(chunk))
                try:
                    import json
                    d = json.loads(chunk)
                    assert isinstance(d, dict)
                    dictConfig(d)
                except:
                    # Apply new configuration.
                    import cStringIO

                    file = cStringIO.StringIO(chunk)
                    try:
                        fileConfig(file)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except:
                        import traceback
                        traceback.print_exc()
        except socket.error, e:
            if not isinstance(e.args, tuple):
                raise
            else:
                errcode = e.args[0]
                if errcode != RESET_ERROR:
                    raise


class LogWriter(object):
    def __init__(self):
        self.stop = False
        self.queue = Queue.Queue()
        writer_thread = threading.Thread(target=self.writer)
        writer_thread.daemon = True
        writer_thread.start()
        self.writer_thread = writer_thread

    def write_log(self, chunk, log_name=None):
        self.queue.put((chunk, log_name))

    def writer(self):
        queue = self.queue
        while not self.stop:
            data = queue.get()
            if data:
                chunk, log_name = data
                obj = self.unPickle(chunk)
                record = logging.makeLogRecord(obj)
                self.handleLogRecord(record, log_name)

    def unPickle(self, data):
        return cPickle.loads(data)

    def handleLogRecord(self, record, name):
        # if a name is specified, we use the named logger rather than the one
        # implied by the record.
        if name is None:
            name = record.name
        logger = logging.getLogger(name)
        # N.B. EVERY record gets logged. This is because Logger.handle
        # is normally called AFTER logger-level filtering. If you want
        # to do filtering, do it at the client end to save wasting
        # cycles and network bandwidth!
        logger.handle(record)

    def shutdown(self):
        self.stop = True
        self.queue.put(None)


class LOGServerMixIn(server.ExternalReactorMixIn):
    log_name = None
    writer = None

    def shutdown_request(self, request):
        pass

    def dispatch(self, sock):
        if sock is self.socket:
            request, client_address = self.get_request()
            self.get_reactor().add_server(self, request)
        else:
            request = sock
        self.handle_log_request(request)

    def get_writer(self):
        writer = self.writer
        if writer is None:
            writer = LogWriter()
            LOGServerMixIn.writer = writer
        return writer

    def handle_log_request(self, sock):
        """
        Handle multiple requests - each expected to be a 4-byte length,
        followed by the LogRecord in pickle format. Logs the record
        according to whatever policy is configured locally.
        """
        chunk = sock.recv(4)
        if len(chunk) < 4:
            self.get_reactor().del_server(sock)
            sock.close()
            return
        slen = struct.unpack('>L', chunk)[0]
        chunk = sock.recv(slen)
        while len(chunk) < slen:
            chunk = chunk + sock.recv(slen - len(chunk))

        writer = self.get_writer()
        writer.write_log(chunk, self.log_name)


class TCPLogServer(LOGServerMixIn, server.TCPServer):
    def __init__(self, server_address, bind_and_activate=True):
        server.TCPServer.__init__(self, server_address, None, bind_and_activate)


class LogConfigServer(server.TCPServer):
    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
        server.TCPServer.__init__(self, server_address, RequestHandlerClass, bind_and_activate)


if hasattr(socket, 'AF_UNIX'):
    class UnixStreamLogServer(LOGServerMixIn, server.UnixStreamServer):
        def __init__(self, server_address, bind_and_activate=True):
            server.UnixStreamServer.__init__(self, server_address, None, bind_and_activate)

    LOG_SERVERS['AF_UNIX'] = UnixStreamLogServer

    class UnixStreamLogConfigServer(server.UnixStreamServer):
        def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
            server.UnixStreamServer.__init__(self, server_address, RequestHandlerClass, bind_and_activate)

    LOG_CONFIG_SERVERS['AF_UNIX'] = UnixStreamLogConfigServer


def make_log_server(server_address, bind_and_activate=True):
    family = address_type(server_address)

    server_cls = LOG_SERVERS.get(family, TCPLogServer)
    return server_cls(server_address, bind_and_activate)


def make_log_config_server(server_address, handler_cls=LoggingConfigRequestHandler, bind_and_activate=True):
    family = address_type(server_address)

    server_cls = LOG_CONFIG_SERVERS.get(family, TCPLogServer)
    return server_cls(server_address, handler_cls, bind_and_activate)
