FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      apache2 apache2-utils libapache2-mod-auth-openidc libapache2-mod-wsgi-py3 \
      ca-certificates python3-venv subversion \
 && rm -rf /var/lib/apt/lists/*

# Trac 1.6 imports pkg_resources, which newer setuptools no longer ship.
# TracXMLRPC has no Python 3 release on PyPI, so it comes from trac-hacks.org.
RUN python3 -m venv /opt/trac \
 && /opt/trac/bin/pip install --no-cache-dir "setuptools<70" wheel \
 && /opt/trac/bin/pip install --no-cache-dir "Trac==1.6" "Jinja2>=2.9.3" "Babel>=2.2,<2.10" \
 && /opt/trac/bin/pip install --no-cache-dir --no-build-isolation \
      "svn+https://trac-hacks.org/svn/xmlrpcplugin/trunk@18657"

COPY trac-api.conf /etc/apache2/sites-available/000-default.conf
COPY trac.wsgi /opt/trac/trac.wsgi
COPY entrypoint.sh /entrypoint.sh
RUN a2enmod auth_openidc wsgi authn_file auth_basic authz_user >/dev/null

EXPOSE 80
ENTRYPOINT ["/entrypoint.sh"]
