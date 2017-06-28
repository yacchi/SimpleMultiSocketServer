# -*- coding:utf8 -*-
from __future__ import absolute_import

import socket
import errno
import logging
from wsgiref.handlers import SimpleHandler as _SimpleHandler
from wsgiref.simple_server import (
    WSGIRequestHandler as _WSGIRequestHandler)
import wsgiref.util

from ..server import make_poller, request_context

logger = logging.getLogger("msocket.server.handler")
wsgiref.util._hoppish = {}.__contains__


# noinspection PyClassHasNoInit
class SimpleHandler(_SimpleHandler):
    http_version = '1.1'

    def finish_chunked_response(self):
        if 'HTTP/1.1' <= self.environ['SERVER_PROTOCOL'] and 'Transfer-Encoding' not in self.headers:
            self.headers['Transfer-Encoding'] = 'chunked'
            try:
                self.send_headers()
                for data in self.result:
                    data = "%d\r\n%s\r\n" % (len(data), data)
                    self.write(data)
                self.write("0\r\n\r\n")
            finally:
                self.close()
        else:
            self.result = [d for d in self.result]
            self.headers['Content-Length'] = len("".join(self.result))
            self.finish_normal_response()

    def finish_normal_response(self):
        _SimpleHandler.finish_response(self)

    def finish_response(self):
        """
        Completes the response and performs the following tasks:

        - Remove the `'ws4py.socket'` and `'ws4py.websocket'`
          environ keys.
        - Attach the returned websocket, if any, to the WSGI server
          using its ``link_websocket_to_server`` method.
        """

        # noinspection PyCompatibility
        try:
            if hasattr(self.result, 'close') and 'Content-Length' not in self.headers:
                self.finish_chunked_response()
            else:
                self.finish_normal_response()
        except socket.error, e:
            if e.errno != errno.EPIPE:
                raise

    def close(self):
        try:
            # noinspection PyUnresolvedReferences
            self.request_handler.log_request(
                self.status.split(' ', 1)[0], self.bytes_sent
            )
        finally:
            _SimpleHandler.close(self)


# noinspection PyClassHasNoInit,PyAttributeOutsideInit,PyUnresolvedReferences
class WSGIKeepAlivedMixIn:
    protocol_version = "HTTP/1.1"
    wsgi_handler = SimpleHandler
    keepalive_timeout = 60
    resolve_ipv6_address = True
    resolve_ipv6_link_local_address = False

    def handle_one_request(self):
        """Handle a single HTTP request"""

        try:
            self.raw_requestline = self.rfile.readline()
            if not self.raw_requestline:
                self.close_connection = 1
                return
            if not self.parse_request():
                # An error code has been sent, just exit
                return

            handler = self.wsgi_handler(
                self.rfile, self.wfile, self.get_stderr(), self.get_environ()
            )
            handler.request_handler = self  # backpointer for logging

            def application(environ, start_response):
                ret = self.server.get_app()(environ, start_response)

                connection = handler.headers.get('Connection')
                if connection:
                    connection = connection.lower()
                    if connection == 'close':
                        self.close_connection = 1
                    elif connection == 'upgrade':
                        self.close_connection = 1
                        request_context.close_connection = False
                    elif connection == 'keep-alive':
                        self.close_connection = 0
                else:
                    conntype = self.headers.get('Connection')
                    if conntype:
                        handler.headers.add_header('Connection', conntype)

                return ret

            handler.run(application)

        except socket.timeout as e:
            # a read or a write timed out.  Discard this connection
            self.log_error("Request timed out: %r", e)
            self.close_connection = 1
            return
        except socket.error as e:
            self.log_error("Request error: %r", e)
            self.close_connection = 1
            return

    def handle(self):
        self.close_connection = 1
        self.handle_one_request()

        if self.close_connection:
            return

        if self.keepalive_timeout == 0:
            while not self.close_connection:
                self.handle_one_request()
            return

        poller = make_poller()
        poller.register(self.rfile)

        while not self.close_connection:
            r = poller.poll(poll_interval=self.keepalive_timeout)
            conn = [True for _ in r]
            if conn:
                self.handle_one_request()
            else:
                self.close_connection = 1

    def address_string(self):
        if hasattr(self, '_address_string_cache'):
            return self._address_string_cache
        host, port = self.client_address[:2]
        # port is None when UnixSocket connection
        if port:
            # ipv4 or resolve ipv6 address enabled
            if ":" not in host or self.resolve_ipv6_address:
                # no link local or link local resolve enabled
                if "%" not in host or self.resolve_ipv6_link_local_address:
                    host = socket.getfqdn(host)
        setattr(self, '_address_string_cache', host)
        return host

    # noinspection PyShadowingBuiltins
    def log_message(self, format, *args):
        logger.info("%s - - %s", self.client_address[0], format % args)


# noinspection PyClassHasNoInit
class WSGIRequestHandler(WSGIKeepAlivedMixIn, _WSGIRequestHandler):
    wbufsize = 1 * 1024 ** 2  # 1MiB

    def get_environ(self):
        env = _WSGIRequestHandler.get_environ(self)
        env['REMOTE_PORT'] = self.client_address[1]
        return env
