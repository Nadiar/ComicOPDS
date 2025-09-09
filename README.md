# 📚 ComicOPDS

ComicOPDS is a lightweight [OPDS 1.2](https://specs.opds.io/opds-1.2) server written in Python, designed for serving **CBZ comics** with metadata extracted from `ComicInfo.xml`.  
It's optimized for large libraries (10k–100k+ comics), supports FastAPI + SQLite + FTS5 search, thumbnail caching, and streaming (OPDS PSE 1.1).

Works great with [Panels for iOS](https://panels.app) and other OPDS readers.

---

## ✨ Features

- 📂 Browse your folder hierarchy
- 🔍 Full-text search (title, series, writer, publisher, year, etc.)
- 📥 Download comics (CBZ)
- 📖 Page streaming (OPDS PSE 1.1)
- 🖼️ Thumbnail extraction & caching (from CBZ covers)
- 📊 Dashboard with stats & charts
- 🧠 Smart Lists (saved search filters)
- 🔐 Optional Basic Auth
- 🐋 Runs easily with Docker / Docker Compose
- ⚡ Fast indexing with SQLite FTS5
- 🔄 File system watching for auto-updates
- 📱 Mobile-optimized dashboard

<a href="https://www.buymeacoffee.com/frederikb" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="41" width="174"></a>

---

## 📋 Documentation

- [Quick Start](docs/quickstart.md)
- [Configuration](docs/configuration.md)
- [API Documentation](docs/api.md)
- [Dashboard](docs/dashboard.md)
- [Smart Lists](docs/smartlists.md)
- [Search](docs/search.md)
- [Client Setup](#-client-setup)
- [Troubleshooting](docs/troubleshooting.md)
- [License](license)

## 📱 Clients

**Supported Clients**

| App                          | Downloads | Search | Streaming | 
| ---------------------------  | --  | -- | -- | 
| KyBook 3 (iOS)               | ✔️  | ✔️  | ❌  |
| Cantook (iOS)                | ✔️  | ❌  | ❌  |
| Panels (iOS)                 | ✔️  |✔️  |✔️  |
| Marvin 3 (iOS)                 | ✔️  | ❌  | ❌  |
| Chunky (iOS)                 | ✔️  | ❌  | ❌  |

### Panels for iOS
1. Open Panels → Library → Connect Service → OPDS
2. **URL**: Your OPDS root (e.g., `https://comics.example.com/`)
3. **Username/Password**: If you enabled Basic Auth
4. Panels will display covers and use your folder structure for browsing

### Client-Specific Notes
- Some clients work better with smaller `PAGE_SIZE` (e.g., 25 instead of 50)
- Page streaming (PSE 1.1) requires client support
- Thumbnail quality may vary between clients

---

## 🔗 Links

- **Repository**: [Gitea](https://gitea.baerentsen.space/FrederikBaerentsen/ComicOPDS)
- **OPDS Specification**: [OPDS 1.2](https://specs.opds.io/opds-1.2)
- **OPDS Page Streaming Extension**: [OPDS PSE 1.1](https://anansi-project.github.io/docs/opds-pse/specs/v1.1)
- **Buy Me a Coffee**: [frederikb](https://www.buymeacoffee.com/frederikb)

---

*Made with ❤️ for comic book enthusiasts*