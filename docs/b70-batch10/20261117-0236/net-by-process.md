# Outside connections by process (2026-11-17, 02:36–02:52)

A second logger ran beside the batch: `sudo ss -tunp` once a second, loopback and the
machine's own address left out, so each connection is listed with the process that
owns it. The vLLM containers use the host network, so their processes appear by name
(`VLLM::EngineCor` below, talking only to the machine itself).

| First seen–last seen | Remote | Process | What it is |
| --- | --- | --- | --- |
| 02:36:41–02:52:22 | 151.101.192.223:443 (Fastly) | jupyter-lab | the remote-access notebook server |
| 02:37:46–02:50:44 | the machine's own address | VLLM::EngineCor | vLLM's processes talking to each other |
| 02:43:48–02:43:49 | 91.189.91.82:80, 91.189.90.40:80, 185.125.190.23:443 (Canonical); 99.86.101.2:443, 3.163.189.119:443 (CloudFront) | http, https | apt's download helpers: the host checking for package updates |
| 02:45:10–02:46:38 | 34.120.177.193:443 (Google Cloud) | grafana | the cloud machine's monitoring dashboard |
| 02:49:47–02:49:53, 02:51:24 | 192.168.2.1:123, 185.125.190.58:123 (UDP) | chronyd | clock synchronisation |

No connection to anywhere outside the machine came from vLLM, Whisper or Haven.

The same Google Cloud address appeared in the 18:03 and 02:13 runs about a minute
after the servers started and was listed there as unexplained; this run shows it
belongs to Grafana, which the cloud machine runs with Prometheus (Prometheus polls
each vLLM server's `/metrics` on 127.0.0.1). Neither is part of Haven, and a clinic
machine would not run them.

Limits: sampled once a second, so a connection shorter than that can be missed; this
is a listing of sockets, not a packet capture with the network cable pulled.
