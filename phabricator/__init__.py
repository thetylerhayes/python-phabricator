"""
python-phabricator
------------------
>>> api = phabricator.Phabricator()
>>> api.user.whoami().userName
'example'

For more endpoints, see https://secure.phabricator.com/conduit/

"""
try:
    __version__ = __import__('pkg_resources') \
        .get_distribution('phabricator').version
except:
    __version__ = 'unknown'

import httplib
import os.path
import hashlib
import simplejson
import time
import urllib
import urlparse

__all__ = ['Conduit', 'Paginator']

# Default phabricator interfaces
INTERFACES = simplejson.loads(open(os.path.join(os.path.dirname(__file__), 'interfaces.json'), 'r').read())

# Load ~/.arcrc if it exists
try:
    ARCRC = simplejson.loads(open(os.path.join(os.path.expanduser('~'), '.arcrc'), 'r').read())
except IOError:
    ARCRC = None

class InterfaceNotDefined(NotImplementedError): pass
class APIError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return '%s: %s' % (self.code, self.message)

class InvalidAccessToken(APIError): pass

class Result(object):
    def __init__(self, response):
        self.response = response

    def __repr__(self):
        return '<%s: %s>' % (self.__class__.__name__, repr(self.response))

    def __iter__(self):
        for r in self.response:
            yield r

    def __getitem__(self, key):
        return self.response[key]

    def __getattr__(self, key):
        return self.response[key]

class Resource(object):
    def __init__(self, api, interface=INTERFACES, node=None, method=None):
        self.api = api
        self.interface = interface
        self.node = node
        self.method = method

    def __getattr__(self, attr):
        if attr in getattr(self, '__dict__'):
            return getattr(self, attr)
        interface = self.interface
        if attr not in interface:
            interface[attr] = {}
        return Resource(self.api, interface[attr], attr, self.node)

    def __call__(self, **kwargs):
        return self._request(**kwargs)

    def _request(self, **kwargs):
        # Check for missing variables
        resource = self.interface
        for k in resource.get('required', []):
            if k not in [ x.split(':')[0] for x in kwargs.keys() ]:
                raise ValueError('Missing required argument: %s' % k)

        api = self.api
        conduit = self.api.conduit

        if conduit:
            # Already authenticated, add session key to json data
            kwargs['__conduit__'] = conduit
        elif self.method == 'conduit' and self.node == 'connect':
            # Not authenticated, requesting new session key
            token = str(int(time.time()))
            kwargs['authToken'] = token
            kwargs['authSignature'] = self.api.generate_hash(token)
        else:
            # Authorization is required, silently auth the user
            self.api.connect()
            kwargs['__conduit__'] = self.api.conduit

        # HACK: Deal with odd backslash escaping for URLs
        if 'host' in kwargs.keys():
            kwargs['host'] = kwargs['host'].replace('/', '\\/')

        url = urlparse.urlparse(api.host)
        if url.scheme == 'https':
            conn = httplib.HTTPSConnection(url.netloc)
        else:
            conn = httplib.HTTPConnection(url.netloc)

        path = url.path + '%s.%s' % (self.method, self.node)

        headers = {
            'User-Agent': 'python-phabricator/%s' % str(self.api.clientVersion),
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        json_data = simplejson.dumps(kwargs, separators=(',',':'))
        params = urllib.quote_plus(json_data).replace('%5C%5C', '%5C')
        data = "params=%s&output=%s" % (params, api.response_format,)

        # TODO: Use HTTP "method" from interfaces.json
        conn.request('POST', path, data, headers)
        response = conn.getresponse()
        result = response.read()
        print result

        # HACK: Handle garbage 'for(;;);' that is leading the json response for some reason...
        if result.startswith('for(;;);'):
            result = result[8:]

        # Process the response back to python
        data = api.formats[api.response_format](result)

        if data['error_code']:
            raise APIError(data['error_code'], data['error_info'])

        return Result(data['result'])


class Phabricator(Resource):
    formats = {
        'json': lambda x: simplejson.loads(x),
    }

    def __init__(self, username=None, certificate=None, host=None, response_format='json', **kwargs):

        # Set values in ~/.arcrc as defaults
        if ARCRC:
            self.host = host if host else ARCRC['hosts'].keys()[0]
            self.username = username if username else ARCRC['hosts'][self.host]['user']
            self.certificate = certificate if certificate else ARCRC['hosts'][self.host]['cert']
        else:
            self.host = host
            self.username = username
            self.certificate = certificate

        self.response_format = response_format
        self.client = 'python-phabricator'
        self.clientVersion = 1
        self.clientDescription = 'Phabricator Python library'
        self.conduit = None

        super(Phabricator, self).__init__(self)

    def _request(self, **kwargs):
        raise SyntaxError('You cannot call the Conduit API without a resource.')

    def connect(self):
        auth = Resource(api=self, method='conduit', node='connect')

        response = auth(user=self.username, host=self.host,
                client=self.client, clientVersion=self.clientVersion)

        self.conduit = {
            'sessionKey': response.sessionKey,
            'connectionID': response.connectionID
        }

    def generate_hash(self, token):
        return hashlib.sha1(token + self.api.certificate).hexdigest()
