# Architecture

## Infrastructure Topology

```mermaid
graph TB
    subgraph Control["MacBook (Control Node)"]
        A[Ansible]
        PC[pre-commit]
    end

    subgraph Pi["Raspberry Pi 4B"]
        subgraph OS["Debian 13 Trixie"]
            SSH[SSH Server]
            UFW[UFW Firewall]
            F2B[fail2ban]
            Docker[Docker Engine]
        end
        subgraph Storage["1.8TB SSD"]
            ROOT["/  ext4 root"]
            SRV["/srv"]
        end
        SD["/boot/firmware  SD card"]
    end

    A -->|SSH| SSH
    Docker --> SRV
```

## Service Architecture

```mermaid
graph TB
    subgraph Docker["Docker Stacks"]
        subgraph Network["network/"]
            AG[AdGuard Home<br/>DNS + ad blocking]
            TF[Traefik<br/>reverse proxy<br/>host network]
        end

        subgraph Core["core/"]
            PORT[Portainer<br/>container mgmt]
            HP[Homepage<br/>dashboard]
        end

        subgraph Media["media/"]
            JF[Jellyfin<br/>media server]
            SON[Sonarr<br/>series automation]
            RAD[Radarr<br/>movie automation]
            QB[qBittorrent<br/>downloads]
        end

        subgraph Mon["monitoring/"]
            GL[Glances<br/>system monitor<br/>host network]
        end
    end

    subgraph Host["Host Services"]
        TS[Tailscale<br/>subnet router + VPN]
    end

    SON -->|API| QB
    RAD -->|API| QB
    JF -->|reads| MediaLib[(media library)]
    QB -->|writes| DL[(downloads)]
    SON -->|imports| MediaLib
    RAD -->|imports| MediaLib
    TF -->|routes *.homelab| Docker
    TS -->|WireGuard tunnel| TF
```

## Network Architecture

```mermaid
graph LR
    subgraph External
        TS_MESH[Tailscale Mesh<br/>Split DNS: *.homelab]
    end

    subgraph LAN["Local Network"]
        CLIENTS[LAN Clients]
    end

    subgraph Pi["Raspberry Pi"]
        AG[AdGuard Home<br/>:53 DNS / :8080 UI]
        TF[Traefik<br/>:80 HTTP / :443 HTTPS<br/>:8180 Dashboard]
        TS[Tailscale<br/>:41641 WireGuard<br/>Subnet Router]
        SERVICES[Backend Services<br/>Jellyfin :8096<br/>Sonarr :8989<br/>Radarr :7878<br/>and more...]
    end

    CLIENTS -->|DNS queries| AG
    AG -->|resolves *.homelab → Pi IP| TF
    TF -->|Host header routing| SERVICES
    TS_MESH -->|WireGuard tunnel| TS
    TS -->|Split DNS *.homelab| TF
    TS -->|Subnet 192.168.1.0/24| Pi

    CLIENTS -.->|jellyfin.homelab:80| TF
    CLIENTS -.->|jellyfin.homelab:8096| SERVICES
```

## Storage Layout

```mermaid
graph TB
    subgraph SSD["1.8TB SSD  /dev/sda"]
        ROOT["/  ext4"]
        SRV["/srv"]

        subgraph srv_tree["/srv subtree"]
            DOCKER["/srv/docker<br/>Docker data root"]
            MEDIA["/srv/media"]
            DL["/srv/downloads"]
            BK["/srv/backups"]

            MOVIES["/srv/media/movies<br/>HEVC library"]
            SERIES["/srv/media/series"]
            INC["/srv/downloads/incomplete"]
            COMP["/srv/downloads/complete"]
        end
    end

    subgraph SD["SD Card"]
        BOOT["/boot/firmware"]
    end

    SRV --> DOCKER
    SRV --> MEDIA
    SRV --> DL
    SRV --> BK
    MEDIA --> MOVIES
    MEDIA --> SERIES
    DL --> INC
    DL --> COMP
```

## Service Dependency Map

```mermaid
graph TD
    AG[AdGuard Home] -->|DNS resolution| ALL[All containers]
    TF[Traefik] -->|reverse proxy| ALL
    TS[Tailscale] -->|VPN tunnel| TF
    PORT[Portainer] -->|manages| DOCKER[Docker Engine]
    SON[Sonarr] -->|sends downloads| QB[qBittorrent]
    RAD[Radarr] -->|sends downloads| QB
    SON -->|imports to| MEDIA[/srv/media]
    RAD -->|imports to| MEDIA
    JF[Jellyfin] -->|reads| MEDIA
    QB -->|writes| DL[/srv/downloads]
    HP[Homepage] -->|health checks| AG
    HP -->|health checks| PORT
    HP -->|health checks| JF
    HP -->|health checks| SON
    HP -->|health checks| RAD
    HP -->|health checks| QB
    HP -->|health checks| TF
    GL[Glances] -->|monitors| DOCKER

    style AG fill:#4a9eff
    style TF fill:#9b59b6
    style HP fill:#f5a623
```

**Deploy order** (respects dependencies):
1. Tailscale — VPN foundation (host-level, not Docker)
2. AdGuard Home — DNS foundation, everything resolves through it
3. Traefik — reverse proxy, routes `*.homelab` to backend services
4. Portainer — container visibility before deploying more stacks
5. qBittorrent — download engine (no deps)
6. Sonarr / Radarr — depend on qBittorrent API
7. Jellyfin — depends on media library populated by Sonarr/Radarr
8. Homepage — dashboard, deployed last so all services exist
9. Glances — monitoring, independent but more useful with services running

## Firewall Architecture

```mermaid
graph TB
    subgraph Incoming["Incoming Traffic"]
        LAN[LAN<br/>192.168.0.0/16]
        TAIL[Tailscale<br/>100.64.0.0/10]
        WAN[Internet]
    end

    subgraph IPTables["iptables chain flow"]
        UFW[UFW Rules<br/>SSH: allow<br/>Default: deny]
        DU[DOCKER-USER Chain<br/>docker0/br-+: pass through<br/>LAN + Tailscale: allow<br/>Others: drop]
        DC[DOCKER Chain<br/>port mapping + NAT]
    end

    subgraph Targets
        HOST[Host Services<br/>SSH]
        CONTAINERS[Docker Containers<br/>AdGuard, Jellyfin, etc.]
        INTERNET[Internet APIs<br/>metadata, trackers, DNS upstream]
    end

    LAN --> UFW --> HOST
    LAN --> DU --> DC --> CONTAINERS
    TAIL --> DU
    WAN -->|blocked| UFW
    WAN -->|dropped| DU
    CONTAINERS -->|outbound via docker0| INTERNET
```

## Data Flow: Media Stack

```mermaid
sequenceDiagram
    participant S as Sonarr/Radarr
    participant Q as qBittorrent
    participant FS as Filesystem
    participant J as Jellyfin

    S->>S: Monitor RSS / search indexers
    S->>Q: Send .torrent via API
    Q->>FS: Download to /srv/downloads/incomplete
    Q->>FS: Move to /srv/downloads/complete
    Q->>S: Notify download complete
    S->>FS: Import + rename to /srv/media/{movies,series}
    J->>FS: Scan /srv/media library
    J->>J: Direct play HEVC (no transcode)
```

## Ansible Role Dependency

```mermaid
graph LR
    BASE[base.yml playbook]
    BASE --> COMMON[common<br/>packages, timezone, sysctl<br/>DNS fallback, WiFi disable]
    BASE --> SEC[security<br/>SSH, UFW, fail2ban]
    BASE --> STOR[storage<br/>/srv directories]
    BASE --> DOCK[docker<br/>engine, compose, DOCKER-USER]

    COMMON --> SEC
    SEC --> STOR
    STOR --> DOCK

    TS[tailscale.yml] --> TAILSCALE[tailscale<br/>host-level VPN<br/>subnet router]
    TF[traefik.yml] --> TRAEFIK[traefik<br/>reverse proxy<br/>depends: docker]
    TRAEFIK -.->|depends| DOCK
```
