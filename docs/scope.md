# 🎯 ComicOPDS Project Scope

This document defines what ComicOPDS is designed to do, what it will not do, and what might be added in the future. This helps set clear expectations and prevents scope creep.

---

## In Scope

ComicOPDS focuses on being the best possible OPDS server for CBZ comics with ComicRack metadata:

### Core OPDS Functionality
- **Clean OPDS 1.2 implementation** - Standards-compliant feeds that work with all major OPDS clients
- **Download support** - Direct CBZ file downloads with HTTP range support
- **Full-text search** - Fast search across most ComicInfo.xml metadata fields
- **Page streaming** - OPDS PSE 1.1 support for individual page access
- **Thumbnail generation** - Cover extraction and caching from CBZ files

### Data Source
- **ComicInfo.xml as single source of truth** - All metadata comes from ComicRack-compatible XML files
- **Folder hierarchy browsing** - Navigate your existing file organization
- **Efficient indexing** - Smart caching and incremental updates

### User Interface
- **Dashboard for library overview** - Simple statistics, charts, and management tools
- **Smart Lists** - Create and manage custom search filters that appear as OPDS folders
- **Basic administration** - Reindex, thumbnail management, error monitoring

---

## Out of Scope

These features will not be added to ComicOPDS. If you need them, consider other solutions:

### Reading Progress & User Data
- **Read/unread tracking** - This metric is not a standard field in ComicInfo.xml and cannot be reliably tracked across applications
- **Reading position sync** - Not part of ComicInfo.xml standard
- **User preferences/bookmarks** - Outside the scope of an OPDS server

> 💡 **Alternative**: Use [Komga](https://komga.org/), [Kometa](https://kometa.wiki/), or [Comixed](https://github.com/comixed/comixed) for reading progress features.

### Multi-User Features  
- **User accounts/authentication beyond Basic Auth** - ComicOPDS is inherently multi-user (no single-user features exist)
- **Per-user libraries or permissions** - Everything is accessible to everyone with credentials
- **Admin user management** - Basic Auth is the only supported authentication method
- **Private vs. shared collections** - All content is shared among authenticated users

### Metadata Management
- **Web interface to edit ComicInfo.xml** - Metadata editing should be done using ComicRack
- **Metadata scraping from ComicVine** - Use ComicRack's excellent scraping capabilities
- **Automatic metadata enhancement** - ComicOPDS only reads existing metadata, never modifies it

> 💡 **Alternative**: Use [ComicRack](http://comicrack.cyolito.com/) for comprehensive metadata management. See my [ComicRack guide](https://comicrack.baerentsen.space/) for best practices.

### Reading Interface
- **Built-in web reader** - ComicOPDS is an OPDS server, not a reading application
- **Reading interface/viewer** - Use dedicated comic readers through OPDS

> 💡 **Alternative**: Use [Komga](https://komga.org/), [Kometa](https://kometa.wiki/), or [Comixed](https://github.com/comixed/comixed) for web-based reading.

### File Format Support
- **CBR support** - RAR format has licensing issues and CBZ is superior
- **PDF support** - PDFs are not optimized for comics and lack proper metadata
- **Other formats** - CBZ is arguably the best format for digital comics

#### Why Use CBZ for Digital Comics (and Not CBR or PDF)

**CBZ** (Comic Book Zip) is the most reliable and future-proof format for digital comics because it's simply a standard `.zip` archive containing sequential images (usually JPG or PNG). This makes it open, easy to manage, and compatible with virtually every comic reader across platforms.  

- **Open & Future-Proof**: ZIP is a universal, well-documented standard that's been around for decades and isn't going away.  
- **Maximum Compatibility**: Works on all major comic readers, e-readers, and even basic image viewers. If needed, you can unzip and read pages directly.  
- **Easy to Edit**: To fix or update a comic, just unzip, replace images, and re-zip. No special tools required.  

By contrast:  
- **CBR** uses the proprietary RAR format. It's less supported, harder to edit, and tied to a commercial license with little archival guarantee. On top of that, **RAR5** (the newer format) complicates things further since not all readers or extraction tools support it, leading to broken compatibility across platforms.  
- **PDF** is bloated and overkill for comics. It often introduces recompression (hurting quality), doesn't scale well to different screen sizes, and is difficult to edit or convert later.  

👉 For longevity, accessibility, and simplicity, **always choose CBZ**.

---

## 🔮 Future Scope (Maybe)

These features might be considered for future versions:

### Enhanced Discovery
- **Recently Added smart list** - Automatic folder based on file timestamps
- **Random comic selection** - Simple discovery feature

### Performance Improvements
- **Database optimizations** - If SQLite becomes a bottleneck

### OPDS Extensions
- **Additional PSE features** - As the standard evolves
- **Enhanced search capabilities** - If clients supports them
- **Better thumbnail handling** - Improved caching strategies

---

## 🧭 Design Philosophy

ComicOPDS follows the Unix philosophy of "do one thing and do it well":

1. **OPDS Server First** - Everything else is secondary to being an excellent OPDS implementation
2. **ComicRack Integration** - Leverage existing tools rather than reinventing them  
3. **Simplicity Over Features** - Prefer simple, reliable functionality over complex features
4. **Performance Focused** - Optimized for large libraries and fast response times
5. **Standards Compliant** - Follow OPDS specifications for maximum compatibility

---

## 🤝 Alternative Solutions

ComicOPDS is designed for a specific use case. If your needs don't match our scope, consider these alternatives:

### For Reading Progress & Multi-User Features
- **[Komga](https://komga.org/)** - Full-featured comic server with web reader and user management
- **[Kometa](https://kometa.wiki/)** - Media management with advanced features
- **[Comixed](https://github.com/comixed/comixed)** - Java-based comic library manager

### For Metadata Management  
- **[ComicRack](http://comicrack.cyolito.com/)** - The gold standard for comic organization and metadata
- **[ComicTagger](https://github.com/comictagger/comictagger)** - Command-line metadata tool
- **[Metron-Tagger](https://github.com/Metron-Project/metron-tagger)** - Command-line metadata tool for [metron.cloud](https://metron.cloud)

### For Different File Formats
- **[Ubooquity](https://vaemendis.net/ubooquity/)** - Supports multiple formats including PDF
- **[Calibre](https://calibre-ebook.com/)** - Universal e-book management (includes comics)

---

## 📝 Contributing to Scope Discussions

If you believe a feature should be reconsidered for inclusion:

1. **Open a GitHub Issue** with detailed reasoning
2. **Explain the use case** - Why is this essential for an OPDS server?
4. **Consider alternatives** - Why existing tools don't solve the problem
5. **Discuss implementation** - How it aligns with our design philosophy

Remember: Just because a feature would be useful doesn't mean it belongs in ComicOPDS. I intentionally keep scope narrow to maintain focus and code quality.

---

*This document should help contributors and users understand what ComicOPDS is and isn't. When in doubt, refer back to the core mission: being the best possible OPDS server for CBZ comics with ComicRack metadata.*