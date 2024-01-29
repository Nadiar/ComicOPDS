from flask import Flask, redirect,url_for,  render_template, send_from_directory, request
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash
from gevent.pywsgi import WSGIServer
import timeit
import sqlite3
import os
from PIL import Image
import zipfile
import gzip
from bs4 import BeautifulSoup
import re
import datetime
import sys
import time
import numpy as np
from pathlib import Path
from io import BytesIO

# for debugging
from pprint import pprint
####

from opds import fromdir
import config,extras

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

@app.route("/", methods=['POST','GET'])
def startpage():
    #result = "Hello, World!"
    config._print(request.method)
    if request.method == 'POST':
        if request.form.get('Create') == 'Create':
            # pass
            config._print("open")
            conn = sqlite3.connect('app.db')
            cursor = conn.cursor()
        
            cursor.execute("create table COMICS (CVDB,ISSUE,SERIES,VOLUME, YEAR, PUBLISHER, TITLE, FILE,PATH,UPDATED,PRIMARY KEY(CVDB))")
            result = cursor.fetchall()
            conn.close()
            config._print("Encrypted")
        elif  request.form.get('Import') == 'Import':
            # pass # do something else
            config._print("Decrypted")
            return redirect(url_for('import2sql'))
        elif request.form.get('Generate') == 'Generate':
            config._print("Generate Covers from Start page")
            return redirect(url_for('generate'))
        else:
            # pass # unknown
            return render_template("first.html")
    elif request.method == 'GET':
        # return render_template("index.html")
        config._print("No Post Back Call")
    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("select * from comics where CVDB in (SELECT CVDB from comics order by RANDOM() LIMIT " + str(config.DEFAULT_SEARCH_NUMBER) + ");")
        result = cursor.fetchall()
    
        pub_list = ["Marvel", "DC Comics","Dark Horse Comics", "Dynamite Entertainment", "Oni Press"]
        count = []
        for i in pub_list:
            cursor.execute("select count(*) from comics where Publisher = '" + i + "';")
            count.append(cursor.fetchone()[0])

        #cursor.execute("SELECT volume, COUNT(volume) FROM comics GROUP BY volume ORDER BY volume;")
        cursor.execute("SELECT year, COUNT(year) FROM comics GROUP BY year ORDER BY year;")
        volume = cursor.fetchall()
        
        

        x = []
        y = []
        for i in volume:
            x.append(i[0])
            y.append(i[1])
        conn.close()

        try:
            total = np.sum(np.array(volume).astype('int')[:,1],axis=0)
            dir_path = r'thumbnails'
            covers = 0
            for path in os.listdir(dir_path):
                if os.path.isfile(os.path.join(dir_path,path)):
                    covers += 1
            config._print("covers: " + str(covers))
        except Exception as e:
            config._print(e)
        return render_template("start.html", first=False,result=result,pub_list=pub_list,count=count,x=x,y=y,total=total,covers=covers)
    except:
        conn.close()

        config._print('first')
        return render_template("start.html",first=True)

#@app.route("/first", methods=['GET', 'POST'])
#def first():
#   return render_template('first.html',result=result) 




@app.route("/healthz")
def healthz():
    return "ok"

@app.route("/generate")
def generate():
    force = request.args.get('force')
    generated = 0
    comiccount = 0
    files_without_comicinfo = 0
    errorcount = 0
    skippedcount = 0
    errormsg = ""
    for root, dirs, files in os.walk(os.path.abspath(config.CONTENT_BASE_DIR)):
        for file in files:
            f = os.path.join(root, file)
            if f.endswith('.cbz'):
                try:
                    comiccount = comiccount + 1
                    s = zipfile.ZipFile(f)
                    filelist = zipfile.ZipFile.namelist(s)
                    if filelist[0] == 'ComicInfo.xml':
                        Bs_data = BeautifulSoup(s.open('ComicInfo.xml').read(), "xml")
                        CVDB=extras.get_cvdb(Bs_data.select('Notes'))
                        if force == 'True':
                            ext = [i for i, x in enumerate(filelist) if re.search("(?i)\.jpg|png|jpeg$", x)]
                            cover = s.open(filelist[ext[0]]).read()
                            
                            image = Image.open(BytesIO(cover))
                            rgb_im = image.convert("RGB")
                            image.thumbnail(config.MAXSIZE,Image.LANCZOS)
                            image.save(config.THUMBNAIL_DIR + "/" + str(CVDB) + ".jpg")

                            # Old way of saving without resize 
                            #c = open(config.THUMBNAIL_DIR + "/" + str(CVDB) + ".jpg", 'wb+')
                            #c.write(cover)
                            #c.close()
                            generated = generated + 1
                        if Path(config.THUMBNAIL_DIR + "/" + str(CVDB) + ".jpg").exists() == False:
                            config._print("generating for " + str(CVDB))
                            try: 
                                ext = [i for i, x in enumerate(filelist) if re.search("(?i)\.jpg|png|jpeg$", x)]
                                #config._print(filelist)
                                #config._print(ext)
                                #config._print(filelist[ext[0]])
                                cover = s.open(filelist[ext[0]]).read()
                                #xyz = [i for i, x in enumerate(filelist) if re.match('*\.py$',x)]
                                #config._print(xyz)
                                image = Image.open(BytesIO(cover))
                                image.thumbnail(config.MAXSIZE,Image.LANCZOS)
                                image.save(config.THUMBNAIL_DIR + "/" + str(CVDB) + ".jpg")
                                generated = generated + 1
                            except Exception as e:
                                errormsg = str(e)
                                print(e)
                        else:
                            skippedcount = skippedcount + 1
                    else:
                        files_withtout_comicinfo = files_without_comicinfo + 1
                except Exception as e:
                    errorcount = errorcount + 1
                    config._print("Error (/generate): " + str(e))
                    config._print(f)
                    errormsg = str(e)
    return "Forced generation: " + str(force) + "<br>Comics: " + str(comiccount) + "<br>Generated: " + str(generated) + "<br>CBZ files without ComicInfo.xml: " + str(files_without_comicinfo) + "<br>Errors: " + str(errorcount) + "<br>Skipped: " + str(skippedcount) + "<br>" + errormsg


@app.route('/import')
def import2sql():
    conn = sqlite3.connect('app.db')
    list = []
    comiccount = 0
    importcount = 0
    coverscount = 0
    skippedcount = 0
    errorcount = 0
    comics_with_errors = []
    start_time = timeit.default_timer()
    for root, dirs, files in os.walk(os.path.abspath(config.CONTENT_BASE_DIR)):
        for file in files:
            f = os.path.join(root, file)
            if f.endswith('.cbz'):
                try:
                    comiccount = comiccount + 1
                    s = zipfile.ZipFile(f)
                    filelist = zipfile.ZipFile.namelist(s)
                    if filelist[0] == 'ComicInfo.xml':
                        filemodtime = os.path.getmtime(f)
                        Bs_data = BeautifulSoup(s.open('ComicInfo.xml').read(), "xml")
                        CVDB=extras.get_cvdb(Bs_data.select('Notes'))
                        ISSUE=Bs_data.select('Number')[0].text
                        SERIES=Bs_data.select('Series')[0].text
                        VOLUME=Bs_data.select('Volume')[0].text
                        YEAR=Bs_data.select('Year')[0].text
                        PUBLISHER=Bs_data.select('Publisher')[0].text
                        try:
                            TITLE=Bs_data.select('Title')[0].text
                        except:
                            TITLE="" #sometimes title is blank.
                        PATH=f 
                        UPDATED=filemodtime
                        #print(UPDATED,file=sys.stdout)
                        #sql="INSERT OR REPLACE INTO COMICS (CVDB,ISSUE,SERIES,VOLUME, PUBLISHER, TITLE, FILE,PATH,UPDATED) VALUES ("+CVDB+",'"+ISSUE+"','"+SERIES+"','"+VOLUME+"','"+PUBLISHER+"','"+TITLE+"','"+file+"','" + f + "','" + UPDATED + "')"
                        #print(sql,file=sys.stdout)
                        #conn.execute(sql);
                        
                        # CREATE TABLE IF MISSING
                        # create table COMICS (CVDB, ISSUE, SERIES,VOLUME,PUBLISHER,TITLE,FILE,PATH,UPDATED,PRIMARY KEY(CVDB))
                        try:
                            query = "SELECT UPDATED FROM COMICS WHERE CVDB = '" + str(CVDB) + "';"
                            savedmodtime = conn.execute(query).fetchone()[0]
                        except:
                            savedmodtime = 0
                        if savedmodtime < filemodtime:
                            conn.execute("INSERT OR REPLACE INTO COMICS (CVDB,ISSUE,SERIES,VOLUME, YEAR, PUBLISHER, TITLE, FILE,PATH,UPDATED) VALUES (?,?,?,?,?,?,?,?,?,?)", (CVDB, ISSUE, SERIES, VOLUME, YEAR, PUBLISHER, TITLE, file, f, UPDATED))
                            conn.commit()
                            config._print("Adding: " + str(CVDB))
                            importcount = importcount + 1
                        elif Path(config.THUMBNAIL_DIR + "/" + str(CVDB) + ".jpg").exists() == False:
                            cover = s.open(filelist[1]).read()
                            c = open(config.THUMBNAIL_DIR + "/" + str(CVDB) + ".jpg", 'wb+')
                            c.write(cover)
                            c.close()
                            coverscount = coverscount + 1
                        else:
                            config._print("Skipping: " + f)
                            skippedcount = skippedcount + 1
                except Exception as e:
                    errorcount = errorcount + 1
                    comics_with_errors.append(f)
                    config._print(e)
    config._print(comics_with_errors)
    conn.close()
    elapsed = timeit.default_timer() - start_time
    elapsed_time = "IMPORTED IN: " + str(round(elapsed,2)) + "s"
    import_stats = elapsed_time + "<br>Comics: " + str(comiccount) + "<br>Imported: " + str(importcount) + "<br>Covers: " + str(coverscount) + "<br>Skipped: " + str(skippedcount) + "<br>Errors: " + str(errorcount)
    return import_stats #+ "<br>" + ['<li>' + x + '</li>' for x in comics_with_errors]

@app.route("/content/<path:path>")
@auth.login_required
def send_content(path):
    #print('content')
    return send_from_directory(config.CONTENT_BASE_DIR, path)

@app.route("/image/<path:path>")
def image(path):
    return send_from_directory(config.THUMBNAIL_DIR,path)

@app.route("/catalog")
@app.route("/catalog/")
@app.route("/catalog/<path:path>")
@auth.login_required
def catalog(path=""):
    #config._print("path: " + path)
    #config._print("root_url: " + request.root_url)
    #config._print("url: " + request.url)
    #config._print("CONTENT_BASE_DIR: " + config.CONTENT_BASE_DIR)
    #print("PRESSED ON")
    start_time = timeit.default_timer()
    #print(request.root_url) 
    c = fromdir(request.root_url, request.url, config.CONTENT_BASE_DIR, path)
    #print("c: ")
    #pprint(vars(c))
    #for x in c.entries:
    #    for y in x.links:
    #        pprint(y.href)
    #print("------")
    elapsed = timeit.default_timer() - start_time
    print("-----------------------------------------------------------------------------------------------------------------------")
    print("RENDERED IN: " + str(round(elapsed,2))+"s")
    
    return c.render()


if __name__ == "__main__":
    #http_server = WSGIServer(("", 5000), app)
    #http_server.serve_forever()
    app.run(debug=True,host='0.0.0.0')
