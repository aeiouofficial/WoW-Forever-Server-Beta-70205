# Current fix branch

Branch: `fix/bnet-tls-proxy`

The local Forever server uses a TLS bridge on `127.0.0.1:1119` and forwards
to BNet on `127.0.0.1:1120`. The bridge must re-encrypt the BNet connection
and must not prepend a PROXY header to the TLS stream:

```text
--bnet-target-tls true
--bnet-proxy-protocol false
```

The previous combination sent a PROXY header before TLS and produced
`SSL Handshake failed wrong version number`, resulting in client error
`BLZ51901016`.

Validation performed locally:

- `tools/Test-AnoWoWLauncher.ps1`: exit `0`
- PowerShell parse check for `Start-ForeverServer.ps1`: passed
- listeners `1119`, `1120`, `8081`, `8082`, `8085`: active

The complete client login to realm selection is still a separate acceptance
test and is not claimed by this branch.
