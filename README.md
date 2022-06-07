# ComicOPDS

ComicOPDS is a lightweight OPDS server written in Python and Flask that allows you to browse your cbz files using OPDS.

## Getting Started

The easiest way to get started is to clone the git reposetory, build the docker image and run docker-compose.

Alternativly you can clone the repo and start the flask server using the main file. This requires a bit of configuration.

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

