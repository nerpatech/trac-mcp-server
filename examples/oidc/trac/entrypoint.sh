#!/bin/sh
set -e

if [ ! -d /var/trac/env ]; then
    mkdir -p /var/trac
    /opt/trac/bin/trac-admin /var/trac/env initenv "OIDC demo" sqlite:db/trac.db
    /opt/trac/bin/trac-admin /var/trac/env config set components tracrpc.* enabled
    /opt/trac/bin/trac-admin /var/trac/env upgrade
    /opt/trac/bin/trac-admin /var/trac/env permission add authenticated \
        TICKET_ADMIN WIKI_ADMIN MILESTONE_ADMIN XML_RPC
    htpasswd -bc /var/trac/htpasswd service-account service-secret
    chown -R www-data:www-data /var/trac
fi

# Log who made each request to the container's output.
ln -sf /proc/1/fd/1 /var/log/apache2/access.log
ln -sf /proc/1/fd/2 /var/log/apache2/error.log
. /etc/apache2/envvars
exec apache2 -DFOREGROUND
