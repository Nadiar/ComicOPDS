import os
from uuid import uuid4
from urllib.parse import quote
from jinja2 import Environment, FileSystemLoader, select_autoescape
from .entry import Entry
from .link import Link
import sqlite3


class Catalog(object):
    def __init__(
        self,
        title,
        id=None,
        author_name=None,
        author_uri=None,
        root_url=None,
        url=None,
    ):
        self.title = title
        self.id = id or uuid4()
        self.author_name = author_name
        self.author_uri = author_uri
        self.root_url = root_url
        self.url = url
        self.entries = []

    def add_entry(self, entry):
        self.entries.append(entry)

    def render(self):
        env = Environment(
            loader=FileSystemLoader(
                searchpath=os.path.join(os.path.dirname(__file__), "templates")
            ),
            autoescape=select_autoescape(["html", "xml"]),
        )
        template = env.get_template("catalog.opds.jinja2")
        return template.render(catalog=self)

def fromsearch(root_url, url, content_base_path, content_relative_path):

    c = Catalog(
        title="test"
    )
        
    return c

def fromdir(root_url, url, content_base_path, content_relative_path):
    
    path = os.path.join(content_base_path, content_relative_path)
    #print(path)
    c = Catalog(
        title=os.path.basename(os.path.dirname(path)), root_url=root_url, url=url
    )
    #print(c.url)
    if not "search" in c.url:
        onlydirs = [
            f for f in os.listdir(path) if not os.path.isfile(os.path.join(path, f))
        ]
    #print(onlydirs)
        for dirname in onlydirs:
            link = Link(
                href=quote(f"/catalog/{content_relative_path}/{dirname}"),
                rel="subsection",
                rpath=path,
                type="application/atom+xml;profile=opds-catalog;kind=acquisition",
            )
            c.add_entry(Entry(title=dirname, id=uuid4(), links=[link]))

    
    if c.url.endswith("/catalog"):
        link2 = Link(
            href=quote(f"/catalog/search"),
            rel="subsection",
            rpath=path,
            type="application/atom+xml;profile=opds-catalog;kind=acquisition",
        )
        c.add_entry(Entry(title="Search",id=uuid4(),links=[link2]))

    if not "search" in c.url:
        onlyfiles = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        #print(onlyfiles)
        for filename in onlyfiles:
            link = Link(
                href=quote(f"/content/{content_relative_path}/{filename}"),
                rel="http://opds-spec.org/acquisition",
                rpath=path,
                type=mimetype(filename),
            )
            c.add_entry(Entry(title=filename.rsplit(".",1)[0], id=uuid4(), links=[link]))
            #fixed issue with multiple . in filename
            #print(c.render()) 
    else:
        search="Man"
        conn = sqlite3.connect('app.db')
        sql="SELECT * from COMICS where SERIES like '%" + search+ "%' or Title like '%" + search+ "%';"
    
        s = conn.execute(sql)
        list=[] 
        for r in s:
            #print(r)
            tUrl=f""+r[7].replace("/home/drudoo/ComicsTest/Comics/","/content/")
            tTitle=r[6]
            link3 = Link(
                #href=quote(f"/content/DC Comics/Earth Cities/Gotham City/Batgirl/Annual/(2012) Batgirl Annual/Batgirl Annual #001 - The Blood That Moves Us [December, 2012].cbz"),
                href=quote(tUrl),
                rel="http://opds-spec.org/acquisition",
                rpath=path,
                type="application/x-cbz",
            )
            c.add_entry(
                Entry(
                    title=tTitle,
                    id=uuid4(),
                    links=[link3]
                    )
                )


    return c



def mimetype(path):
    extension = path.split(".")[-1].lower()
    if extension == "pdf":
        return "application/pdf"
    elif extension == "epub":
        return "application/epub"
    elif extension == "mobi":
        return "application/mobi"
    elif extension == "cbz":
        return "application/x-cbz"
    else:
        return "application/unknown"
