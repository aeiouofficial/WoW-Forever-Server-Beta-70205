@echo off
cd /d D:\AnoCore-Server

echo Starting MariaDB...
net start "ForeverServer Database" 2>nul

echo Starting CRL Server...
start "AnoCore - CRL (8087)" python -I -m http.server 8087 --bind 127.0.0.1 --directory D:\AnoCore-Server\tls\public

echo Starting Bnetserver...
cd D:\AnoCore-Server\server
start "AnoCore - bnetserver" bnetserver.exe -c bnetserver.conf

echo Starting Worldserver...
start "AnoCore - worldserver" worldserver.exe -c worldserver.conf

echo Starting TLS Bridge...
cd D:\AnoCore-Server\bridge
start "AnoCore - TLS bridge" ForeverTlsBridge.exe --certificate-thumbprint FA0C400BF678DC7A87F4F061331B19D1FE42E55C --listen-address 127.0.0.1 --bnet-listen 1119 --bnet-target 1120 --bnet-target-tls true --bnet-proxy-protocol false --rest-listen 8081 --rest-target 8082 --rest-target-tls true

echo All servers launched.
exit
