import os
from werkzeug.security import generate_password_hash
from sys import platform

#CONTENT_BASE_DIR = os.getenv("CONTENT_BASE_DIR", "/library") #docker

if platform == "linux" or platform == "linux2":
    CONTENT_BASE_DIR = os.getenv("CONTENT_BASE_DIR", "/home/drudoo/ComicsTest/Comics") #linux
elif platform == "win32":
    CONTENT_BASE_DIR = os.getenv("CONTENT_BASE_DIR", "/Comics/ComicRack") #windows
    #CONTENT_BASE_DIR = os.getenv("CONTENT_BASE_DIR", "testlibrary") #windows test library


# Added folder for thumbnails. These are loaded as covers for the files.
THUMBNAIL_DIR = os.getenv("THUMBNAIL_DIR",'thumbnails')

# If using Windows, insert the drive letter of your comics here. 
# Both the script and comics needs to be on the same drive.
WIN_DRIVE_LETTER = 'B'

# If using custom searches, then insert the default amout of results here. 
# It is also possible to override this in the json file.
DEFAULT_SEARCH_NUMBER = 10

# Debug output
# False: no print out in terminal
# True: logs are printet to terminal
DEBUG = True

# Max thumbnail size
MAXSIZE = (500,500)

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
