# Chapter 2 · Networking for the Cluster and the Migration

Most failed OpenShift installs and most failed migrations are network problems: a missing DNS record, a route that does not exist, a firewall in the path, or a VLAN that is not on the trunk. This chapter gives you the five networking ideas you need and shows each one on the real lab.

## Five ideas, in vSphere terms

| IDEA | WHAT IT IS | VSPHERE YOU ALREADY KNOW |
|---|---|---|
| IP address + prefix | `198.51.100.34/27` = address `.34` in a network of 32 addresses | The IP you type in the VM's guest customization |
| Gateway | The router address that leads out of your subnet | The default gateway in the same screen |
| VLAN | A numbered virtual network on the same cable | The VLAN ID on a port group |
| Route | "To reach network X, go through router Y" | ESXi has these too (`esxcli network ip route`) |
| DNS | Name to IP lookup | How vCenter finds `esx001.example.com` |

### Reading a /27

A prefix says how many bits are the network. `/24` = 256 addresses, `/27` = 32 addresses. The network team gave this lab three /27 VLANs:

| NAME | VLAN | NETWORK | GATEWAY | USED FOR |
|---|---|---|---|---|
| PG-OCP-NODES | 100 | 198.51.100.32/27 (.32 to .63) | .33 | OpenShift nodes .34 to .36, API VIP .37, Ingress VIP .38 |
| PG-VM-120 | 120 | 198.51.100.64/27 (.64 to .95) | .65 | VM network NAD `vlan-120` |
| PG-VM-130 | 130 | 198.51.100.96/27 (.96 to .127) | .97 | VM network NAD `vlan-130` |
| 110 (old) | 110 | 198.51.100.0/27 | .1 | Bastion and the source VM's port group |

## The bastion's view of the network

**admin@bastion — real capture**

```console
[admin@bastion ~]$ ip -br addr
lo               UNKNOWN        127.0.0.1/8 ::1/128 
ens33            UP             198.51.100.24/24 fe80::250:56ff:fe00:1/64 
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ ip route
default via 198.51.100.1 dev ens33 proto static metric 100 
198.51.100.0/24 dev ens33 proto kernel scope link src 198.51.100.24 metric 100 
198.51.100.32/27 via 198.51.100.1 dev ens33 proto static metric 100 
198.51.100.64/27 via 198.51.100.1 dev ens33 proto static metric 100 
198.51.100.96/27 via 198.51.100.1 dev ens33 proto static metric 100 
```

How to read it, line by line:

1. `default via 198.51.100.1`: anything not listed below goes to the router `.1`.
2. `198.51.100.0/24 dev ens33 ... scope link`: the bastion believes **all** of 198.51.100.x is directly on its own wire. That was true before the network team cut the /24 into /27 VLANs. It is not true any more.
3. The three `/27 via 198.51.100.1` routes are the fix. Linux always picks the **most specific** match, so traffic to `.34` matches `/27` (27 bits) before `/24` (24 bits) and goes through the router, which knows where VLAN 100 is.

> **GOTCHA — REAL ISSUE** After the nodes moved to PG-OCP-NODES the bastion could not reach them: it tried to ARP for `.34` on its own wire (because of the old `/24` mask) and nobody answered. Adding the three static routes fixed it without touching the bastion's own IP:
> `sudo nmcli connection modify ens33 +ipv4.routes "198.51.100.32/27 198.51.100.1, 198.51.100.64/27 198.51.100.1, 198.51.100.96/27 198.51.100.1" && sudo nmcli connection up ens33`

**admin@bastion — real capture**

```console
[admin@bastion ~]$ ping -c 2 -W 2 198.51.100.33 | tail -2
2 packets transmitted, 2 received, 0% packet loss, time 1017ms
rtt min/avg/max/mdev = 0.525/0.544/0.563/0.019 ms
```

The node-network gateway answers in about half a millisecond: the route works.

## DNS: the records OpenShift needs

An OpenShift cluster needs exactly these names before installation. In this lab they are A records in the Active Directory DNS zone `example.com`, under the cluster name `ocp1`.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ grep nameserver /etc/resolv.conf
nameserver 203.0.113.251
nameserver 203.0.113.252
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ for h in api api-int test.apps ocp-node-1 ocp-node-2 ocp-node-3; do printf "%-12s %s\n" $h $(dig +short $h.ocp1.example.com); done
api          198.51.100.37
api-int      198.51.100.37
test.apps    198.51.100.38
ocp-node-1    198.51.100.34
ocp-node-2    198.51.100.35
ocp-node-3    198.51.100.36
```

| RECORD | POINTS TO | WHO USES IT |
|---|---|---|
| `api.<cluster>.<domain>` | API VIP .37 | You, `oc`, the web console |
| `api-int.<cluster>.<domain>` | API VIP .37 | The nodes themselves |
| `*.apps.<cluster>.<domain>` (wildcard) | Ingress VIP .38 | Every Route: console, OAuth, MTV UI, your apps |
| node names | node IPs | Nice to have; the installer uses the MACs and IPs |

`test.apps` resolving to `.38` proves the **wildcard** works: nobody created a record called `test`.

```powershell
# Reference: how the records were created (PowerShell on the jump host, AD DNS)
PS C:\> $z='example.com'; $s='dc01.example.com'
PS C:\> Add-DnsServerResourceRecordA -ComputerName $s -ZoneName $z -Name 'api.ocp1'     -IPv4Address 198.51.100.37
PS C:\> Add-DnsServerResourceRecordA -ComputerName $s -ZoneName $z -Name 'api-int.ocp1' -IPv4Address 198.51.100.37
PS C:\> Add-DnsServerResourceRecordA -ComputerName $s -ZoneName $z -Name '*.apps.ocp1'  -IPv4Address 198.51.100.38
PS C:\> 1..3 | % { Add-DnsServerResourceRecordA -ComputerName $s -ZoneName $z -Name "ocp-node-$_.ocp1" -IPv4Address "198.51.100.$(33+$_)" }
```

> **GOTCHA — REAL ISSUE** The first plan named the cluster `ocp`, giving `api.ocp.example.com`. That name already belonged to another team's cluster in the same AD zone. Creating the record would have pointed their API at your VIP. Always `nslookup api.<name>.<domain>` **before** choosing a cluster name; this lab became `ocp1` for that reason.

> **GOTCHA — REAL ISSUE** The bastion originally used public DNS (4.2.2.2, 8.8.8.8). Public DNS cannot resolve `example.com`, so `openshift-install wait-for` and `oc` could not find `api.ocp1.example.com`. Setting the bastion's DNS to the domain controllers (`nmcli connection modify ens33 ipv4.dns "203.0.113.251 203.0.113.252"`) fixed it.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ dig +short -x 198.51.100.37; dig +short vcenter.example.com
203.0.113.9
```

The first query (reverse lookup of the API VIP) returned nothing: there is **no PTR record**. OpenShift does not need one. The second shows vCenter at `203.0.113.9`, which MTV will need to resolve from inside the cluster.

## Ports: can I reach what I need?

MTV talks to vCenter on 443 (inventory, and disk reads without VDDK) and to each ESXi host on 902 (disk reads with VDDK). Test before you migrate, not during.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ for t in vcenter.example.com:443 esxi01.example.com:902 esxi02.example.com:902 api.ocp1.example.com:6443; do timeout 4 bash -c "</dev/tcp/${t%:*}/${t#*:}" && echo "$t open" || echo "$t CLOSED"; done
vcenter.example.com:443 open
esxi01.example.com:902 open
esxi02.example.com:902 open
api.ocp1.example.com:6443 open
```

> **TIP** `bash -c "</dev/tcp/HOST/PORT"` is a port test that needs no extra package; it is the Linux `Test-NetConnection -Port`. `nc -zv HOST PORT` does the same if `nmap-ncat` is installed.

| FROM | TO | PORT | WHY |
|---|---|---|---|
| Bastion | API VIP | 6443 | `oc` |
| Your browser | Ingress VIP | 443 | Web console, MTV UI |
| OpenShift nodes | vCenter | 443 | MTV inventory and (without VDDK) disk data |
| OpenShift nodes | every ESXi host | 902 | VDDK disk data (NBD) |
| OpenShift nodes | NFS server | 2049 | Persistent volumes |
| OpenShift nodes | NTP | UDP 123 | Time; etcd and certificates break without it |

## Time

**admin@bastion — real capture**

```console
[admin@bastion ~]$ chronyc sources 2>/dev/null | head -8
MS Name/IP address         Stratum Poll Reach LastRx Last sample               
===============================================================================
^? 99-28-14-242.lightspeed.>     0  10     0     -     +0ns[   +0ns] +/-    0ns
^? time.cloudflare.com           0  10     0     -     +0ns[   +0ns] +/-    0ns
^? h134-215-155-177.mdtnwi.>     0  10     0     -     +0ns[   +0ns] +/-    0ns
^? time2.trtnw.net               0  10     0     -     +0ns[   +0ns] +/-    0ns
^? tick.chi1.ntfo.org            0  10     0     -     +0ns[   +0ns] +/-    0ns
^? owners.kjsl.com               0  10     0     -     +0ns[   +0ns] +/-    0ns
```

> **GOTCHA — REAL ISSUE** This capture is a live problem, not a historic one. `^?` and `Reach 0` mean the bastion has **never** reached any of its public NTP servers: UDP 123 to the internet is blocked from 198.51.100.x. The cluster nodes do not have this problem because their agent config lists the lab NTP servers (`203.0.113.117`, `.118` and the two DCs, Chapter 3). Fix the bastion the same way: replace the `pool` line in `/etc/chrony.conf` with `server 203.0.113.117 iburst` and `server 203.0.113.118 iburst`, then `sudo systemctl restart chronyd` and check that `^*` appears.

> **GOTCHA — REAL ISSUE** VMs on ESXi host `esxi04` lose their network in this lab. A DRS *must not run on* rule keeps the bastion, the OCP nodes and the source VMs off it (`a DRS rule script`). If a migration or a node suddenly cannot reach anything, check which host it runs on before debugging Linux.

> **EXAM TIP** EX280 and EX316 do not ask you to build DNS, but they do expect you to diagnose it: `oc get route`, `dig`, `curl -kv https://<route-host>`, and reading why a Route has no endpoints.

> **LAB — DO IT ON THE BASTION** (1) Prove that `random-name.apps.ocp1.example.com` resolves to `.38`. (2) Remove nothing, but explain with `ip route get 198.51.100.34` which route the kernel picks and why. (3) Fix the bastion's NTP as described above and capture `chronyc sources` again.

## Check yourself

**Q1. The bastion has `198.51.100.0/24 dev ens33` and also `198.51.100.32/27 via 198.51.100.1`. Which one is used for 198.51.100.35?**
The `/27` route. *Logic:* longest-prefix match. 27 matching bits beat 24, so the packet goes to the router instead of being ARPed on the local wire.

**Q2. Why does OpenShift need a wildcard record and not one record per application?**
Every Route gets a new host name under `*.apps`, created at any time by any user. *Logic:* the Ingress VIP is the single entry point; the router (HAProxy) reads the host name in the request and forwards it, so DNS only needs to send all names to that one VIP.

**Q3. `api` and `api-int` point to the same VIP. Why have two names?**
`api` is for clients, `api-int` for the nodes. *Logic:* in larger designs they can point to different load balancers (external vs internal) without changing any node configuration.

**Q4. Port 902 to `esxi03` is closed but 443 to vCenter is open. Will a migration work?**
Without VDDK, yes (disk data comes through vCenter on 443, slowly). With VDDK, VMs on `esxi03` will fail at disk transfer. *Logic:* VDDK opens NBD connections directly to the ESXi host that owns the VM, on 902.

**Q5. `chronyc sources` shows `^?` with `Reach 0` for every source. What does that mean and why should you care before installing OpenShift?**
No source has ever answered. *Logic:* OpenShift certificates and etcd leader election depend on agreed time; nodes that drift apart fail TLS checks and etcd health. Give the nodes reachable NTP servers at install time.
