## 📱 Clients

**Supported Clients**

| App                          | Downloads | Search | Streaming | Streaming Protocol |
| ---------------------------  | --  | -- | -- | -- |
| Panels (iOS)                 | ✔️  |✔️  |✔️  | PSE 1.1 (OPDS 1.2) |
| KyBook 3 (iOS)               | ✔️  | ✔️  | ❌  | — |
| Cantook (iOS)                | ✔️  | ❌  | ❌  | — |
| Marvin 3 (iOS)                 | ✔️  | ❌  | ❌  | — |
| Chunky (iOS)                 | ✔️  | ❌  | ❌  | — |

### Panels for iOS

Panels works with ComicOPDS via OPDS 1.2 with automatic content negotiation support.

1. Open Panels → Library → Connect Service → OPDS
2. **URL**: Your OPDS root (e.g., `https://comics.example.com/`)
3. **Username/Password**: If you enabled Basic Auth
4. Panels will display covers and use your folder structure for browsing
5. Full-text search and page streaming (PSE 1.1) are supported

### Client-Specific Notes
- Some clients work better with smaller `PAGE_SIZE` (e.g., 25 instead of 50)
- **OPDS 1.2 clients** use OPDS PSE 1.1 for page streaming (e.g., Panels)
- **OPDS 2.0 clients** should use DiViNa manifests (Readium Web Publication Manifest) for page streaming. The server provides a `self` link with type `application/divina+json` on each publication entry, pointing to a per-book manifest with `readingOrder` containing individual page image links.
- Thumbnail quality may vary between clients


## Notes on Clients and HTTPS

Most OPDS clients (Panels, Marvin, Thorium, Chunky, etc.) work fine over plain `http://` when you are on your local network or connected with a standard VPN (e.g. WireGuard, OpenVPN).  

⚠️ **iOS requirement**: When connecting outside your local network, iOS apps typically require `https://`.  
This is because of Apple's [App Transport Security (ATS)](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity) rules, which block insecure external requests.  

- If you use a traditional VPN, you can usually keep using `http://` on the VPN tunnel.  
- If you use Tailscale, be aware that iOS does not treat Tailscale connections as a "real VPN" and still enforces HTTPS.  
- The solution is to set up a valid HTTPS certificate (e.g. via Let's Encrypt) and reverse proxy (Traefik, Nginx, Caddy, etc.).

👉 This project does not provide networking or HTTPS setup guides, but be aware that if you plan to access ComicOPDS outside your LAN (or via Tailscale on iOS), you'll need to serve it over HTTPS.
