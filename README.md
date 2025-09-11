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

## 📱 Clients

**Supported Clients**

| App                          | Downloads | Search | Streaming | 
| ---------------------------  | --  | -- | -- | 
| Panels (iOS)                 | ✔️  |✔️  |✔️  |
| KyBook 3 (iOS)               | ✔️  | ✔️  | ❌  |
| Cantook (iOS)                | ✔️  | ❌  | ❌  |
| Marvin 3 (iOS)                 | ✔️  | ❌  | ❌  |
| Chunky (iOS)                 | ✔️  | ❌  | ❌  |

---

## 📋 Documentation

- 🚀 [Quick Start](docs/quickstart.md)
- 🔧 [Configuration](docs/configuration.md)
- 🌐 [API & Endpoints](docs/api.md)
- 📊 [Dashboard](docs/dashboard.md)
- 🧠 [Smart Lists](docs/smartlists.md)
- 🔍 [Search](docs/search.md)
- 📱 [Client Setup](docs/clients.md)
- 🎯 [Project Scope](docs/scope.md)
- 🛠️ [Troubleshooting](docs/troubleshooting.md)
- 📄 [License](license.md)

---

## 💪 Stress Test

ComicOPDS has been stress tested using **170k+ CBZ files** generated using [CBZGenerator](https://gitea.baerentsen.space/FrederikBaerentsen/CBZGenerator). 

**Performance Results:**
- **Initial scan**: ~10 minutes for full library indexing
- **Thumbnail generation**: ~30 minutes (depending on hardware)
- **Hardware**: Tested on low-powered Intel N100 CPU with no performance issues
- **Search**: Very fast response times with SQLite FTS5 even at this scale

The server remains responsive during indexing and handles concurrent OPDS requests without degradation. Memory usage stays reasonable even with large libraries.

---

## 🔗 Links

- **Repository**: [Gitea](https://gitea.baerentsen.space/FrederikBaerentsen/ComicOPDS)
- **OPDS Specification**: [OPDS 1.2](https://specs.opds.io/opds-1.2)
- **OPDS Page Streaming Extension**: [OPDS PSE 1.1](https://anansi-project.github.io/docs/opds-pse/specs/v1.1)
- **Buy Me a Coffee**: [frederikb](https://www.buymeacoffee.com/frederikb)

---

*Made with ❤️ for comic book enthusiasts*
