import os
from werkzeug.security import generate_password_hash

#CONTENT_BASE_DIR = os.getenv("CONTENT_BASE_DIR", "/library") #docker
#CONTENT_BASE_DIR = os.getenv("CONTENT_BASE_DIR", "/home/drudoo/ComicsTest/Comics") #linux
CONTENT_BASE_DIR = os.getenv("CONTENT_BASE_DIR", "/Comics/ComicRack") #windows
#CONTENT_BASE_DIR = os.getenv("CONTENT_BASE_DIR", "testlibrary") #windows test library

THUMBNAIL_DIR = os.getenv("THUMBNAIL_DIR",'thumbnails')

WIN_DRIVE_LETTER = 'B'
DEFAULT_SEARCH_NUMBER = 10

DEBUG = True

def _print(arg):
    if DEBUG:
        print(arg)

TEENYOPDS_ADMIN_PASSWORD = os.getenv("TEENYOPDS_ADMIN_PASSWORD", None)
users = {}
if TEENYOPDS_ADMIN_PASSWORD:
    users = {
        "admin": generate_password_hash(TEENYOPDS_ADMIN_PASSWORD),
    }
else:
    print(
        "WANRNING: admin password not configured - catalog will be exposed was public"
    )
