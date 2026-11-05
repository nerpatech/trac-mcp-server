import os

os.environ["TRAC_ENV"] = "/var/trac/env"
os.environ["PYTHON_EGG_CACHE"] = "/tmp"

from trac.web.main import dispatch_request

application = dispatch_request
