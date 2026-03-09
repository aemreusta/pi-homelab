# TODO

## [ ] Glances → Beszel migrasyonu

Glances'ı kaldırıp yerine Beszel kurmak.

**Yapılacaklar:**

- `ansible/roles/glances` rolünü sil
- `ansible/playbooks/glances.yml` playbook'unu sil
- `Makefile`'dan `glances` target'ını kaldır
- `ansible/roles/beszel` rolü oluştur (standalone mod, tek container)
- Homepage dashboard'una Beszel widget'ı ekle
- UFW kurallarını güncelle (Beszel default port: 8090)

## [ ] Uptime Kuma kurulumu

Servis uptime izleme (HTTP, TCP, DNS, Docker container check).

**Yapılacaklar:**

- `ansible/roles/uptime_kuma` rolü oluştur
- `ansible/playbooks/uptime-kuma.yml` playbook'u oluştur
- `Makefile`'a `uptime-kuma` target'ı ekle
- UFW kuralı ekle (default port: 3001)
- Homepage dashboard'una Uptime Kuma widget'ı ekle

## [ ] Speedtest Tracker kurulumu

Periyodik internet hız testi ve geçmiş grafikleri.

**Yapılacaklar:**

- `ansible/roles/speedtest_tracker` rolü oluştur (image: `lscr.io/linuxserver/speedtest-tracker`)
- `ansible/playbooks/speedtest-tracker.yml` playbook'u oluştur
- `Makefile`'a `speedtest-tracker` target'ı ekle
- UFW kuralı ekle (default port: 8765)
- Homepage dashboard'una Speedtest widget'ı ekle
