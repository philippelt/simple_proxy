# simple_proxy
Simple lightweight python http debugging proxy to help track REST or SOAP web services problems.

export PROXY_LOCAL, localhost:8880 by default

export PROXY_TARGET, mandatory except the port 80 by default

Then run ./simple_proxy.py to forward all incoming request on PROXY_LOCAL to PROXY_TARGET.

HTTP packets are unchanged except for references to PROXY_LOCAL replaced by PROXY_TARGET to ensure 100% transparent proxy behavior (a true man-in-the-middle behavior :-) )
