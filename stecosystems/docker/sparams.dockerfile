FROM ubuntu

LABEL maintainer="Marat <marat@cmu.edu>"

RUN apt-get update && apt-get install -y python python3 python-setuptools \
    python3-setuptools libssl-dev python-pip python3-pip \
    python-distutils-extra python3-distutils-extra \
    && pip install Cython \
    && pip3 install Cython \
    && useradd -m user && chown user:user /home/user \
    && apt-get install -y sudo \
    && echo 'user ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/user \
    && mkdir /home/user/package && chown user:user /home/user/package

COPY sparams.sh /home/user
COPY sparams.py /home/user

CMD ["bash", "/home/user/sparams.sh"]

