from flask import Flask, send_from_directory, request
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
@app.route("/healthz")
def healthz():
    return "ok"

@app.route('/import')
def import2sql():
    conn = sqlite3.connect('app.db')
    list = []

    start_time = timeit.default_timer()
    for root, dirs, files in os.walk(os.path.abspath(config.CONTENT_BASE_DIR)):
        for file in files:
            f = os.path.join(root, file)
            #try:
            try:
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
                    TITLE="" #sometimes title is blank.
                PATH=f 
                UPDATED=str(datetime.datetime.now())
                #print(UPDATED,file=sys.stdout)
                #sql="INSERT OR REPLACE INTO COMICS (CVDB,ISSUE,SERIES,VOLUME, PUBLISHER, TITLE, FILE,PATH,UPDATED) VALUES ("+CVDB[0]+",'"+ISSUE+"','"+SERIES+"','"+VOLUME+"','"+PUBLISHER+"','"+TITLE+"','"+file+"','" + f + "','" + UPDATED + "')"
                #print(sql,file=sys.stdout)
                #conn.execute(sql);
                conn.execute("INSERT OR REPLACE INTO COMICS (CVDB,ISSUE,SERIES,VOLUME, PUBLISHER, TITLE, FILE,PATH,UPDATED) VALUES (?,?,?,?,?,?,?,?,?)", (CVDB[0], ISSUE, SERIES, VOLUME, PUBLISHER, TITLE, file, f, UPDATED))
                conn.commit()
            except:
                print(f,file=sys.stdout)
    
    conn.close()
    elapsed = timeit.default_timer() - start_time
    print(elapsed)

    return str(elapsed)

@app.route("/content/<path:path>")
@auth.login_required
def send_content(path):
    return send_from_directory(config.CONTENT_BASE_DIR, path)

@app.route("/catalog")
@app.route("/catalog/<path:path>")
@auth.login_required
def catalog(path=""):
    start_time = timeit.default_timer()
    print(request.root_url) 
    c = fromdir(request.root_url, request.url, config.CONTENT_BASE_DIR, path)
    elapsed = timeit.default_timer() - start_time
    print(elapsed)
    
    return c.render()


if __name__ == "__main__":
    #http_server = WSGIServer(("", 5000), app)
    #http_server.serve_forever()
    app.run(debug=True,host='0.0.0.0')
