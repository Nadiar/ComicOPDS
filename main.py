from flask import Flask, render_template, send_from_directory, request
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash
from gevent.pywsgi import WSGIServer
import timeit
import sqlite3
import os
import zipfile
import gzip
from bs4 import BeautifulSoup
import re
import datetime
import sys
import time

from opds import fromdir
import config

app = Flask(__name__, static_url_path="", static_folder="static")
auth = HTTPBasicAuth()

@auth.verify_password
def verify_password(username, password):
    if not config.TEENYOPDS_ADMIN_PASSWORD:
        return True
    elif username in config.users and check_password_hash(
        config.users.get(username), password
    ):
        return username


@app.route("/")
def startpage():
    #result = "Hello, World!"
    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()
    cursor.execute("select * from comics LIMIT " + str(config.DEFAULT_SEARCH_NUMBER) + ";")
    result = cursor.fetchall()
    conn.close()
    return render_template("start.html", result=result)

@app.route("/healthz")
def healthz():
    return "ok"

@app.route('/import')
def import2sql():
    conn = sqlite3.connect('app.db')
    list = []
    comiccount = 0
    importcount = 0
    skippedcount = 0
    errorcount = 0
    comics_with_errors = []
    start_time = timeit.default_timer()
    for root, dirs, files in os.walk(os.path.abspath(config.CONTENT_BASE_DIR)):
        for file in files:
            f = os.path.join(root, file)
            #try:
            if f.endswith('.cbz'):
                try:
                    comiccount = comiccount + 1
                    s = zipfile.ZipFile(f)
                    filelist = zipfile.ZipFile.namelist(s)
                    if filelist[0] == 'ComicInfo.xml':
                        filemodtime = os.path.getmtime(f)
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
                            TITLE="" #sometimes title is blank.
                        PATH=f 
                        UPDATED=filemodtime
                        #print(UPDATED,file=sys.stdout)
                        #sql="INSERT OR REPLACE INTO COMICS (CVDB,ISSUE,SERIES,VOLUME, PUBLISHER, TITLE, FILE,PATH,UPDATED) VALUES ("+CVDB[0]+",'"+ISSUE+"','"+SERIES+"','"+VOLUME+"','"+PUBLISHER+"','"+TITLE+"','"+file+"','" + f + "','" + UPDATED + "')"
                        #print(sql,file=sys.stdout)
                        #conn.execute(sql);
                        
                        # CREATE TABLE IF MISSING
                        # create table COMICS (CVDB, ISSUE, SERIES,VOLUME,PUBLISHER,TITLE,FILE,PATH,UPDATED,PRIMARY KEY(CVDB))
                        try:
                            query = "SELECT UPDATED FROM COMICS WHERE CVDB = '" + str(CVDB[0]) + "';"
                            savedmodtime = conn.execute(query).fetchone()[0]
                        except:
                            savedmodtime = 0
                        #print(savedmodtime)
                        #print(float(savedmodtime))
                        #print(type(savedmodtime))
                        #print(type(filemodtime))
                        if savedmodtime < filemodtime:
                            #print(str(savedmodtime) + " is less than " + str(filemodtime))
                            
                            #print(str(CVDB[0]) + " - s: " + str(savedmodtime))
                            #print(str(CVDB[0]) + " - f: " + str(filemodtime))

                            cover = s.open(filelist[1]).read()
                            c = open(config.THUMBNAIL_DIR + "/" + str(CVDB[0]) + ".jpg", 'wb+')
                            c.write(cover)
                            c.close()

                            conn.execute("INSERT OR REPLACE INTO COMICS (CVDB,ISSUE,SERIES,VOLUME, PUBLISHER, TITLE, FILE,PATH,UPDATED) VALUES (?,?,?,?,?,?,?,?,?)", (CVDB[0], ISSUE, SERIES, VOLUME, PUBLISHER, TITLE, file, f, UPDATED))
                            conn.commit()
                            #print("Adding: " + str(CVDB[0]))
                            importcount = importcount + 1
                        else:
                        #    print("Skipping: " + str(CVDB[0]))
                            skippedcount = skippedcount + 1
                except Exception as e:
                    errorcount = errorcount + 1
                    comics_with_errors.append(f)
                    print(e)
                    #print(f,file=sys.stdout)
    print(comics_with_errors)
    conn.close()
    elapsed = timeit.default_timer() - start_time
    elapsed_time = "IMPORTED IN: " + str(round(elapsed,2)) + "s"
    import_stats = elapsed_time + "<br>Comics: " + str(comiccount) + "<br>Imported: " + str(importcount) + "<br>Skipped: " + str(skippedcount) + "<br>Errors: " + str(errorcount)
    return import_stats #+ "<br>" + ['<li>' + x + '</li>' for x in comics_with_errors]

@app.route("/content/<path:path>")
@auth.login_required
def send_content(path):
    print('content')
    return send_from_directory(config.CONTENT_BASE_DIR, path)

@app.route("/image/<path:path>")
def image(path):
    return send_from_directory(config.THUMBNAIL_DIR,path)

@app.route("/catalog")
@app.route("/catalog/")
@app.route("/catalog/<path:path>")
@auth.login_required
def catalog(path=""):
    #print("PRESSED ON")
    start_time = timeit.default_timer()
    #print(request.root_url) 
    c = fromdir(request.root_url, request.url, config.CONTENT_BASE_DIR, path)
    elapsed = timeit.default_timer() - start_time
    print("-----------------------------------------------------------------------------------------------------------------------")
    print("RENDERED IN: " + str(round(elapsed,2))+"s")
    
    return c.render()


if __name__ == "__main__":
    #http_server = WSGIServer(("", 5000), app)
    #http_server.serve_forever()
    app.run(debug=True,host='0.0.0.0')
