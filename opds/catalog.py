import os
from uuid import uuid4
from urllib.parse import quote
from jinja2 import Environment, FileSystemLoader, select_autoescape
from .entry import Entry
from .link import Link
import sqlite3,json
import config
import extras

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

    if os.path.basename(content_relative_path) == "":
        c = Catalog(
            title="Comics",
            root_url=root_url,
            url=url
            )
    else:
        c = Catalog(
            title=extras.xmlesc(os.path.basename(content_relative_path)),
            root_url=root_url, 
            url=url
        )
    #title=os.path.basename(os.path.dirname(path)), root_url=root_url, url=url

    ##########WORKING AREA###########
    searchArr=[]
    if c.url.endswith("/catalog"):
        with open('test.json') as fi:
            data=json.load(fi)
            print("--> LOADED FILE") # try and get this as low as possible.
    #searchArr=["Girl","Bat","Part One"]
    
            for e in data:
                for key, value in e.items():
                    searchArr.append(key)
    print(searchArr) 
    ######################

    
    

    if not "search" in c.url:
        onlydirs = [
            f for f in os.listdir(path) if not os.path.isfile(os.path.join(path, f))
        ]
    #print(onlydirs)
        for dirname in onlydirs:
            link = Link(
                href=quote(f"/catalog/{content_relative_path}/{dirname}").replace('//','/'), #windows fix
                rel="subsection",
                rpath=path,
                type="application/atom+xml;profile=opds-catalog;kind=acquisition",
            )
            c.add_entry(Entry(title=extras.xmlesc(dirname), id=uuid4(), links=[link]))

    
    if c.url.endswith("/catalog"):
        
        for i in searchArr:

            link2 = Link(
                href=quote(f"/catalog/search["+i+"]"),
                rel="subsection",
                rpath=path,
                type="application/atom+xml;profile=opds-catalog;kind=acquisition",
            )
            c.add_entry(Entry(title="["+i+"]",id=uuid4(),links=[link2]))

    if not "search" in c.url:
        onlyfiles = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        onlyfiles.sort()
        for filename in onlyfiles:
            link = Link(
                href=quote(f"/content/{content_relative_path}/{filename}"),
                rel="http://opds-spec.org/acquisition",
                rpath=path,
                type=mimetype(filename),
            )

            #c.add_entry(Entry(title=filename.rsplit(".",1)[0], id=uuid4(), links=[link]))
            c.add_entry(Entry(title=extras.xmlesc(filename).rsplit(".",1)[0], id=uuid4(), links=[link]))
        
            #fixed issue with multiple . in filename
            #print(c.render()) 
    else:
        with open('test.json') as fi:
            data=json.load(fi)
            config._print("--> LOADED 2 FILE") # try and get this as low as possible.
        for e in data:
            for key, value in e.items():
                config._print(key)
                searchArr.append(key)
        for i in searchArr:
            config._print("i (in searchArr): " + i)
            config._print("quote i: " + quote(f""+i))
            if quote(f""+i) in c.url:
                conn = sqlite3.connect('app.db')
                for e in data:
                    config._print("e (in data): " + str(e))
                    for key, value in e.items():
                        config._print("key: " + key)
                        if key == i:
                            config._print("key <" + str(key) + "> matches <" + str(i) + ">")
                            query="SELECT * FROM COMICS where "
                            for h in value:
                                first=True
                                for j,k in h.items():
                                    
                                    if j == 'SQL':
                                        query = query + k
                                    if k != '' and j != "SQL":
                                        config._print(j)
                                        config._print(k)
                                        config._print(query)
                                        if not first and j != 'limit':
                                            query = query + "and "
                                            config._print(query)
                                        if type(k) == list:
                                            config._print(k)
                                            if j == "series" or j == "title":
                                                firstS = True
                                                query = query + "("
                                                config._print(query)
                                                for l in k:
                                                    if not firstS:
                                                        query = query + "or "
                                                        config._print(query)
                                                    query = query + j + " like '%" + l + "%' "
                                                    config._print(query)
                                                    if firstS: 
                                                        firstS = False
                                                query = query + ") "
                                                config._print(query)
                                            else:
                                                query = query + j + " in (" 
                                                config._print(query)
                                                firstL = True
                                                for l in k:
                                                    if not firstL: 
                                                        query = query + ","
                                                        config._print(query)
                                                    query = query + "'" + str(l) + "'"
                                                    config._print(query)
                                                    if firstL:
                                                        firstL = False
                                                query = query + ") "
                                                config._print(query)

                                        elif j != 'limit':
                                            query = query + j + " like '%" + str(k) + "%' "
                                            config._print(query)
                                        elif j == 'limit':
                                            config.DEFAULT_SEARCH_NUMBER = k
                                        else:
                                            print(">>>>>>>>>>>ERROR THIS SHOULD NOT HAPPEN<<<<<<<<<<<")
                                        if first:
                                            first = False
                                                                                
                                query = query + " order by series asc, cast(issue as unsigned) asc "
                                if config.DEFAULT_SEARCH_NUMBER != 0:
                                    query = query + "LIMIT " + str(config.DEFAULT_SEARCH_NUMBER) + ";"
                                else:
                                    query = query + ";"
                                break
                        else: 
                            config._print("key <" + str(key) + "> DOES NOT match <" + str(i) + ">")
                    
                config._print("----> " + query)
                                
                sql = query
                #sql="SELECT * from COMICS where SERIES like '%" + i+ "%' or Title like '%" + i+ "%';"
                #config._print(sql)
                s = conn.execute(sql)
                #list=[] 
                for r in s:
                    #config._print(r)
                    tUrl=f""+r[7].replace('\\','/').replace(config.WIN_DRIVE_LETTER + ':','').replace(config.CONTENT_BASE_DIR,"/content")
                    #config._print(tUrl)
                    tTitle=r[6]
                    link3 = Link(
                        #href=quote(f"/content/DC Comics/Earth Cities/Gotham City/Batgirl/Annual/(2012) Batgirl Annual/Batgirl Annual #001 - The Blood That Moves Us [December, 2012].cbz"),
                        href=quote(tUrl),
                        rel="http://opds-spec.org/acquisition",
                        rpath=path,
                        type="application/x-cbz",
                    )
                    #config._print(link3.href)
                    c.add_entry(
                        Entry(
                            title=tTitle,
                            id=uuid4(),
                            links=[link3]
                            )
                        )
    #print(c.title)
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
