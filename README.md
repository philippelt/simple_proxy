# simple_proxy
Simple lightweight python http debugging proxy to help track REST or SOAP web services problems.

export PROXY_LOCAL, localhost:8880 by default

export PROXY_TARGET, mandatory except the port 80 by default

export PROXY_SSL=1 if target has to be reached using ssl, the default port becoming 443

Then run ./simple_proxy.py to forward all incoming request on PROXY_LOCAL (always http) to PROXY_TARGET (http/https).

HTTP packets are unchanged except for references to PROXY_LOCAL replaced by PROXY_TARGET to ensure 100% transparent proxy behavior (a true man-in-the-middle behavior :-) even in gzip compressed bodies)
