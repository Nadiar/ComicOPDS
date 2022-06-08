import sqlite3
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import re
import datetime

def createdb():
    print(1)
    conn = sqlite3.connect('../test_database.db') 
    c = conn.cursor()

    c.execute('''
          CREATE TABLE IF NOT EXISTS comics
          (
			[book_id] TEXT PRIMARY KEY,
			[book_path] TEXT, 
			[series] TEXT,
			[number] TEXT,
			[count] INTEGER,
			[volume] TEXT,
			[seriesgroup] TEXT,
			[notes] TEXT,
			[year] INTEGER,
			[month] INTEGER,
			[day] INTEGER
		  )
          ''')
    conn.commit()

def dropdb():
	conn = sqlite3.connect('../test_database.db')
	c = conn.cursor()
	c.execute('DROP TABLE COMICS')
	conn.commit()

def loaddata():
    count = 0
    book_id,book_path,series,number="","","",""
    count=0
    volume,seriesgroup,notes="","",""
    year,month,date=0,0,0
    
    tree = ET.parse('../ComicDb_small.xml')
    root = tree.getroot()

    for child in root:
        print(child.tag,child.attrib)
        if child.tag == 'Books':
            for grandchild in child:
                print(grandchild.tag,grandchild.attrib)
                for ggchild in grandchild:
                    print(ggchild.tag,ggchild.attrib)
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
loaddata()
