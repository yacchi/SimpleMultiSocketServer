# -*- coding:utf8 -*-

try:
    py3k = False
    import SocketServer
    socketserver = SocketServer
    string_class = basestring

except ImportError:
    py3k = True
    import socketserver
    string_class = str


def address_type(address):
    '''
    Return the types of the address

    This can be 'AF_INET', 'AF_UNIX', or 'AF_PIPE'
    '''
    if type(address) == tuple:
        if ':' in address[0]:
            return 'AF_INET6'
        return 'AF_INET'
    elif isinstance(address, string_class) and address.startswith('\\\\'):
        return 'AF_PIPE'
    elif isinstance(address, string_class):
        return 'AF_UNIX'
    else:
        raise ValueError('address type of %r unrecognized' % address)
