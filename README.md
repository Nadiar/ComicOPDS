# ComicOPDS

ComicOPDS is a lightweight OPDS server written in Python and Flask that allows you to browse your cbz files using OPDS.

## Getting Started

The easiest way to get started is to clone the git reposetory, build the docker image and run docker-compose.

Alternativly you can clone the repo and start the flask server using the main file. This requires a bit of configuration.

### Prerequisites

- **All comic files needs to be cbz.**
  This doens't work with cbr. I see no reason to use cbr and as such, this project wont support it.

- **All comics must be properly tagged.** 
  This means every cbz file must contain a `ComicInfo.xml` file. You can use various tools to  

### Docker

#### Prerequisites

- Docker
- Docker-compose

#### Installing

First clone the git repo.

    git clone https://gitea.baerentsen.space/ComicOPDS

Then go to the folder

    cd ComicOPDS

Next build the image

    docker build . -t comicopds

Adjust the docker-compose file:

    <add docker compose example with config options and drive mapping>

Run docker-compose

    docker-compose up

### Manual Install

To manually install the flask server you need to install the python requirements.

#### Prerequisites

- Python3.x

#### Installing

    python3 -m pip install -r requirements.txt

#### Change configs

In the `config.py` file you need to change like 4 from `"/library"` to your comic library. This has only been tested on Debian.

#### Running

    python3 main.py

## Supported Readers

Any reader that supports OPDS should work, however the following have been verified to work/not work

| App                                                                                                   | iOS |
| ----------------------------------------------------------------------------  | --- |
| KyBook 3 (iOS)                                                             | ✔️  |
| Aldiko Next (iOS)                                                                                             | ❌  |
| PocketBook (iOS)                                                               | ✔️  |
| Moon+ Reader (Android) | ✔️       |
| Panels (iOS)                                                               | ✔️  |
| Marvin (iOS)                                                             | ✔️  |
| Chunky (iOS)                                                             | ✔️  |

# Notes

5865 files in 359 seconds
