import zipfile
from bs4 import BeautifulSoup
import os

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
        "links",
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
        #print(kwargs["links"][0].get("rpath"))
        #print("--end entry.py")

        if kwargs["links"][0].get("type") == 'application/x-cbz':
            f=self.links[0].get("rpath")+"/"+self.title+".cbz"
            if os.path.exists(f): 
                s = zipfile.ZipFile(f)
                data=BeautifulSoup(s.open('ComicInfo.xml').read(), "xml")
            #print(data)
            #print(kwargs["links"][0])
            #print(data.select('Series')[0].text)
            #print(kwargs["links"][0].get("rpath"))
                if data.select('Series')[0].text in kwargs["links"][0].get("rpath"):
                    releasedate=data.select('Year')[0].text+"-"+data.select('Month')[0].text.zfill(2)+"-"+data.select('Day')[0].text.zfill(2)
                    try:
                        self.title = "#"+data.select('Number')[0].text.zfill(2) + ": " + data.select('Title')[0].text + " (" + releasedate + ")"
                    except:
                        self.title = "#"+data.select('Number')[0].text.zfill(2) + " (" + releasedate + ")"
            #print(self.title)
                else:
                    self.title = kwargs["title"]

            else:
                self.title = kwargs["title"]
            #self.title = data.select('Title')[0].text
            

    def get(self, key):
        return self._data.get(key, None)

    def set(self, key, value):
        self.validate(key, value)
        self._data[key] = value
