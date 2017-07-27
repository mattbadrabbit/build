.PHONY: clean iso packages customfiles

iso: /dev/md10 mfsbsd-2.3/conf/rc.conf packages customfiles
	cd mfsbsd-2.3; \
	make iso BASE=/tmp/cdrom/usr/freebsd-dist/ PKG_STATIC=/usr/local/sbin/pkg-static MFSROOT_MAXSIZE=250m

/usr/local/bin/wget:
	pkg install wget

mfsbsd-2.3/conf/rc.conf: mfsbsd-2.3 rc.conf
	cp rc.conf mfsbsd-2.3/conf/rc.conf

mfsbsd-2.3: /usr/local/bin/wget
	wget https://github.com/mmatuska/mfsbsd/archive/2.3.tar.gz
	tar -xzvf 2.3.tar.gz
	rm 2.3.tar.gz

FreeBSD-11.0-RELEASE-amd64-disc1.iso: /usr/local/bin/wget
	wget https://download.freebsd.org/ftp/releases/amd64/amd64/ISO-IMAGES/11.0/FreeBSD-11.0-RELEASE-amd64-disc1.iso

/dev/md10: FreeBSD-11.0-RELEASE-amd64-disc1.iso
	(mdconfig -a -t vnode -u 10 -f FreeBSD-11.0-RELEASE-amd64-disc1.iso 2> /dev/null && mkdir -p /tmp/cdrom && mount_cd9660 /dev/md10 /tmp/cdrom) || true

clean:
	cd mfsbsd-2.3; \
	make clean; \
	rm -f mfsbsd-11.0-RELEASE-p1-amd64.iso

packages:
	mkdir -p packages
	pkg fetch --yes --dependencies --output packages node
	pkg fetch --yes --dependencies --output packages nginx
	cp -f packages/All/* mfsbsd-2.3/packages/

customfiles:
	rm -rf mfsbsd-2.3/customfiles
	cp -r customfiles mfsbsd-2.3/

install:
	pkg install python36
	python3.6 -m ensurepip
	pip3 install pyyaml
	pip3 install jinja2
