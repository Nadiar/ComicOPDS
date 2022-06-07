import zipfile
from bs4 import BeautifulSoup
import time
import config
import os,sys
import time
import sqlite3
import timeit
import re
import datetime

conn = sqlite3.connect('app.db')
list = []

start_time = timeit.default_timer()
for root, dirs, files in os.walk(os.path.abspath(config.CONTENT_BASE_DIR)):
    for file in files:
        f = os.path.join(root, file)
        #try:
        if f.endswith(".cbz"):
            print("CBZ: " + f)
            s = zipfile.ZipFile(f)
            #s = gzip.GzipFile(f)
            Bs_data = BeautifulSoup(s.open('ComicInfo.xml').read(), "xml")
            #print(Bs_data.select('Series')[0].text, file=sys.stderr)
            #print(Bs_data.select('Title')[0].text, file=sys.stderr)
            CVDB=re.findall('(?<=\[CVDB)(.*)(?=].)', Bs_data.select('Notes')[0].text)
            #list.append('CVDB'+CVDB[0] + ': '  + Bs_data.select('Series')[0].text + "(" + Bs_data.select('Volume')[0].text + ") : " + Bs_data.select('Number')[0].text  )
            #print(list, file=sys.stdout)
        
            ISSUE=Bs_data.select('Number')[0].text
            SERIES=Bs_data.select('Series')[0].text
            VOLUME=Bs_data.select('Volume')[0].text
            PUBLISHER=Bs_data.select('Publisher')[0].text
            try:
                TITLE=Bs_data.select('Title')[0].text
            except:
                TITLE=""
            PATH=f 
            UPDATED=str(datetime.datetime.now())
            #print(UPDATED,file=sys.stdout)
            #sql="INSERT OR REPLACE INTO COMICS (CVDB,ISSUE,SERIES,VOLUME, PUBLISHER, TITLE, FILE,PATH,UPDATED) VALUES ("+CVDB[0]+",'"+ISSUE+"','"+SERIES+"','"+VOLUME+"','"+PUBLISHER+"','"+TITLE+"','"+file+"','" + f + "','" + UPDATED + "')"
            #print(sql,file=sys.stdout)
            conn.execute("INSERT OR REPLACE INTO COMICS (CVDB,ISSUE,SERIES,VOLUME, PUBLISHER, TITLE, FILE,PATH,UPDATED) VALUES (?,?,?,?,?,?,?,?,?)", (CVDB[0], ISSUE, SERIES, VOLUME, PUBLISHER, TITLE, file, f, UPDATED))
            conn.commit()
        else:
            print("NOT CBZ: " + f)

conn.close()
elapsed = timeit.default_timer() - start_time
print(elapsed)
