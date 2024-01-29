import zipfile
from bs4 import BeautifulSoup
import os
import re

import extras
import config

class Entry(object):
    valid_keys = (
        "id",
        "url",
        "title",
        "content",
        "downloadsPerMonth",
        "updated",
        "identifier",
        "date",
        "rights",
        "summary",
        "dcterms_source",
        "provider",
        "publishers",
        "contributors",
        "languages",
        "subjects",
        "oai_updatedates",
        "authors",
        "formats",
        "size",
        "links",
        "cover",
        "covertype"
    )

    required_keys = ("id", "title", "links")

    def validate(self, key, value):
        if key not in Entry.valid_keys:
            raise KeyError("invalid key in opds.catalog.Entry: %s" % (key))

    def __init__(self, **kwargs):
        for key, val in kwargs.items():
            self.validate(key, val)

        for req_key in Entry.required_keys:
            if not req_key in kwargs:
                raise KeyError("required key %s not supplied for Entry!" % (req_key))
        self.id = kwargs["id"]
        self.title = kwargs["title"]
        self.links = kwargs["links"]
        self._data = kwargs
        
        #print(">>entry.py")
        #print(kwargs)
        print(kwargs["title"])
        #print(kwargs["links"][0].get("rpath"))
        #print("--end entry.py")
        try:
            if kwargs["links"][0].get("type") == 'application/x-cbz':
                f=self.links[0].get("rpath")+"/"+self.title+".cbz"
                if os.path.exists(f): 
                    s = zipfile.ZipFile(f)
                    self.size = extras.get_size(f, 'mb')
                    data=BeautifulSoup(s.open('ComicInfo.xml').read(), features="lxml")
                    #self.cover=s.open('P00001.jpg').read()

                    if data.select('Writer') != []:
                        self.authors = data.select('Writer')[0].text.split(",")
                    else:
                        config._print("No Writer found: " + str(data.select('Writer')))
                        
                    self.cover = "/image/" + extras.get_cvdb(data.select('Notes')) + ".jpg"
                    #if data.select('Title') != []:
                    #    self.title = data.select('Title')[0]
                        
                    #    print(data.select('Title')[0])
                    title = data.select('Title')[0].text.replace("&","&amp;")
                    kwargs["title"] = title
                    print(title)
                    if data.select('Summary') != []:
                        #print(data.select('Summary')[0].text)
                        self.summary = data.select('Summary')[0]
                    else:
                        config._print("No Summary found: " + str(data.select('Summary')))
                    

                #print(data)
                #print(kwargs["links"][0])
                #print(data.select('Series')[0].text)
                #print(kwargs["links"][0].get("rpath"))
                    if data.select('Series')[0].text in kwargs["links"][0].get("rpath"):
                        releasedate=data.select('Year')[0].text+"-"+data.select('Month')[0].text.zfill(2)+"-"+data.select('Day')[0].text.zfill(2)
                        try:
                            self.title = "#"+data.select('Number')[0].text.zfill(2) + ": " + title + " (" + releasedate + ") [" + str(self.size) + "MB]"
                        except:
                            self.title = "#"+data.select('Number')[0].text.zfill(2) + " (" + releasedate + ") [" + str(self.size) + "MB]"
                #print(self.title)
                    else:
                        self.title = title

                else:
                    self.title = kwargs["title"]
                #self.title = data.select('Title')[0].text
        except Exception as e:
            config._print(e)
    def get(self, key):
        return self._data.get(key, None)

    def set(self, key, value):
        self.validate(key, value)
        self._data[key] = value
