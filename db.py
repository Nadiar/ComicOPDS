import sqlite3
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import re
import datetime

def createdb():
    conn = sqlite3.connect('../test_database.db') 
    c = conn.cursor()

    c.execute('''
          CREATE TABLE IF NOT EXISTS comics
          (
            [book_id] TEXT PRIMARY KEY,
            [book_path] TEXT, 
            [series] TEXT,
            [seriesgroup] TEXT,
            [number] TEXT,
            [count] INTEGER,
            [volume] TEXT,
            [notes] TEXT,
            [year] INTEGER,
            [month] INTEGER,
            [day] INTEGER,
            [writer] TEXT,
            [penciller] TEXT,
            [inker] TEXT,
            [letterer] TEXT,
            [colorist] TEXT,
            [coverartist] TEXT,
            [publisher] TEXT,
            [genre] TEXT,
            [pagecount] INTEGER,
            [languageiso] TEXT,
            [scaninformation] TEXT,
            [pages] INTEGER,
            [added] TEXT,
            [filesize] INTEGER,
            [filemodifiedtime] TEXT,
            [filecreationtime] TEXT
          )
          ''')
    conn.commit()

def dropdb():
    conn = sqlite3.connect('../test_database.db')
    c = conn.cursor()
    c.execute('DROP TABLE COMICS')
    conn.commit()

def checkempty(v,t):
    r=""
    try: 
        r=v.find(t).text
    except:
        pass
    return r

def loaddata():
    conn = sqlite3.connect('../test_database.db')
    c = conn.cursor()
    
    book_id,book_path,series,seriesgroup,number="","","","",""
    count=0
    volume,seriesgroup,notes="","",""
    year,month,day=0,0,0
    writer,penciller,inker,letterer,colorist,coverartist,publiser,genre="","","","","","","",""
    pagecount=0
    languageiso,scaninformation="",""
    pages=0
    added=""
    filesize=0
    filemodificationtime,filecreationtime="",""
    
    tree = ET.parse('../ComicDb_small.xml')
    root = tree.getroot()

    for child in root:
        #print("child: ", child.tag,child.attrib)
        if child.tag == 'Books':
            for grandchild in child:
                #print("grandchild: ",grandchild.tag,grandchild.attrib)
                #print(grandchild.attrib)
                #print(type(grandchild.attrib))
                book_id=grandchild.attrib['Id']
                book_path=grandchild.attrib['File']
                #for i,j in grandchild.attrib.items():
                #    print(i,j)
                #    #print(i,i["Id"])
                #series=grandchild.attrib['Series'].text
                #print(series)
                #print(grandchild[0].tag)
                #series=grandchild.find('Series').text
                series=checkempty(grandchild,'Series')
                number=checkempty(grandchild,'Number')
                count=checkempty(grandchild,'Count')
                seriesgroup=checkempty(grandchild,'SeriesGroup')
                notes=checkempty(grandchild,'Notes')
                year=checkempty(grandchild,'Year')
                month=checkempty(grandchild,'Month')
                day=checkempty(grandchild,'Day')
                writer=checkempty(grandchild,'Writer')
                penciller=checkempty(grandchild,'Penciller')
                inker=checkempty(grandchild,'Inker')
                letterer=checkempty(grandchild,'Letterer')
                
                c.execute("INSERT OR REPLACE INTO COMICS (book_id,book_path,series,number,count,seriesgroup,notes,year,month,day,writer,penciller, inker,letterer) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(book_id,book_path,series,number,count,seriesgroup,notes,year,month,day,writer,penciller,inker,letterer))
                conn.commit()

                #for ggchild in grandchild:
                #    print(ggchild.tag)
                #    print(ggchild.text)
                #print("----")

        #for books in child.findall('Book'):
            #print(books,type(books))
            #print(books.tag, books.attrib)






    #with open('ComicDb_small.xml', 'r') as f:
    #    contents = f.read()
    #    Bs_data = BeautifulSoup(contents, 'xml')
    #    for i in Bs_data.find_all('Book'):
    #        #print(i)
    #        try:
    #            book_id = i.find('Book',{"Id"}).text
    #            print(book_id)
    #        except:
    #            pass
    #        try:
    #            series=i.select('Series')[0].text
    #        except:
    #            pass
#dropdb()
#createdb()

loaddata()

