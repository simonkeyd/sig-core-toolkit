# These are shared utilities used

import os
import json
import hashlib
import shlex
import subprocess
import yaml
import requests
import boto3
import xmltodict
import productmd.treeinfo
import empanadas
from empanadas.common import Color

class ArchCheck:
    """
    Arches and their files
    """
    archfile = {
        'x86_64': [
                'isolinux/vmlinuz',
                'images/grub.conf',
                'EFI/BOOT/BOOTX64.EFI'
        ],
        'aarch64':  [
                'EFI/BOOT/BOOTAA64.EFI'
        ],
        'ppc64le': [
                'ppc/bootinfo.txt',
                'ppc/ppc64/vmlinuz'
        ],
        's390x': [
                'generic.ins',
                'images/generic.prm'
        ]
    }

class Shared:
    """
    Quick utilities that may be commonly used
    """
    @staticmethod
    def get_checksum(path, hashtype, logger):
        """
        Generates a checksum from the provided path by doing things in chunks.
        This way we don't do it in memory.
        """
        try:
            checksum = hashlib.new(hashtype)
        except ValueError:
            logger.error("Invalid hash type: %s" % hashtype)
            return False

        try:
            input_file = open(path, "rb")
        except IOError as e:
            logger.error("Could not open file %s: %s" % (path, e))
            return False

        while True:
            chunk = input_file.read(8192)
            if not chunk:
                break
            checksum.update(chunk)

        input_file.close()
        stat = os.stat(path)
        base = os.path.basename(path)
        # This emulates our current syncing scripts that runs stat and
        # sha256sum and what not with a very specific output.
        return "%s: %s bytes\n%s (%s) = %s\n" % (
                base,
                stat.st_size,
                hashtype.upper(),
                base,
                checksum.hexdigest()
        )

    @staticmethod
    def treeinfo_new_write(
            file_path,
            distname,
            shortname,
            release,
            arch,
            time,
            repo
        ):
        """
        Writes really basic treeinfo, this is for single repository treeinfo
        data. This is usually called in the case of a fresh run and each repo
        needs one. This basic info may be overwritten later either by lorax
        data or a full run.
        """
        ti = productmd.treeinfo.TreeInfo()
        ti.release.name = distname
        ti.release.short = shortname
        ti.release.version = release
        ti.tree.arch = arch
        ti.tree.build_timestamp = time
        # Variants (aka repos)
        variant = productmd.treeinfo.Variant(ti)
        variant.id = repo
        variant.uid = repo
        variant.name = repo
        variant.type = "variant"
        variant.paths.repository = "."
        variant.paths.packages = "Packages"
        ti.variants.add(variant)
        ti.dump(file_path)

    @staticmethod
    def treeinfo_modify_write(data, imagemap, logger):
        """
        Modifies a specific treeinfo with already available data. This is in
        the case of modifying treeinfo for primary repos or images.
        """
        arch = data['arch']
        variant = data['variant']
        variant_path = data['variant_path']
        checksum = data['checksum']
        distname = data['distname']
        fullname = data['fullname']
        shortname = data['shortname']
        release = data['release']
        timestamp = data['timestamp']

        os_or_ks = ''
        if '/os/' in variant_path or not imagemap['disc']:
            os_or_ks = 'os'
        if '/kickstart/' in variant_path:
            os_or_ks = 'kickstart'

        image = os.path.join(variant_path)
        treeinfo = os.path.join(image, '.treeinfo')
        discinfo = os.path.join(image, '.discinfo')
        mediarepo = os.path.join(image, 'media.repo')
        #imagemap = self.iso_map['images'][variant]
        primary = imagemap['variant']
        repos = imagemap['repos']
        is_disc = False

        if imagemap['disc']:
            is_disc = True
            discnum = 1

        # load up productmd
        ti = productmd.treeinfo.TreeInfo()
        ti.load(treeinfo)

        # Set the name
        ti.release.name = distname
        ti.release.short = shortname
        # Set the version (the initial lorax run does this, but we are setting
        # it just in case)
        ti.release.version = release
        # Assign the present images into a var as a copy. For each platform,
        # clear out the present dictionary. For each item and path in the
        # assigned var, assign it back to the platform dictionary. If the path
        # is empty, continue. Do checksums afterwards.
        plats = ti.images.images.copy()
        for platform in ti.images.images:
            ti.images.images[platform] = {}
            for i, p in plats[platform].items():
                if not p:
                    continue
                if 'boot.iso' in i and is_disc:
                    continue
                ti.images.images[platform][i] = p
                ti.checksums.add(p, checksum, root_dir=image)

        # stage2 checksums
        if ti.stage2.mainimage:
            ti.checksums.add(ti.stage2.mainimage, checksum, root_dir=image)

        if ti.stage2.instimage:
            ti.checksums.add(ti.stage2.instimage, checksum, root_dir=image)

        # If we are a disc, set the media section appropriately.
        if is_disc:
            ti.media.discnum = discnum
            ti.media.totaldiscs = discnum

        # Create variants
        # Note to self: There's a lot of legacy stuff running around for
        # Fedora, ELN, and RHEL in general. This is the general structure,
        # apparently. But there could be a chance it'll change. We may need to
        # put in a configuration to deal with it at some point.
        #ti.variants.variants.clear()
        for y in repos:
            if y in ti.variants.variants.keys():
                vari = ti.variants.variants[y]
            else:
                vari = productmd.treeinfo.Variant(ti)

            vari.id = y
            vari.uid = y
            vari.name = y
            vari.type = "variant"
            if is_disc:
                vari.paths.repository = y
                vari.paths.packages = y + "/Packages"
            else:
                if y == primary:
                    vari.paths.repository = "."
                    vari.paths.packages = "Packages"
                else:
                    vari.paths.repository = "../../../" + y + "/" + arch + "/" + os_or_ks
                    vari.paths.packages = "../../../" + y + "/" + arch + "/" + os_or_ks + "/Packages"

            if y not in ti.variants.variants.keys():
                ti.variants.add(vari)

            del vari

        # Set default variant
        logger.info('Writing treeinfo')
        ti.dump(treeinfo, main_variant=primary)
        # Set discinfo
        logger.info('Writing discinfo')
        Shared.discinfo_write(timestamp, fullname, arch, discinfo)
        # Set media.repo
        logger.info('Writing media.repo')
        Shared.media_repo_write(timestamp, fullname, mediarepo)

    @staticmethod
    def write_metadata(
            timestamp,
            datestamp,
            fullname,
            release,
            compose_id,
            file_path
    ):

        metadata = {
                "header": {
                    "name": "empanadas",
                    "version": empanadas.__version__,
                    "type": "toolkit",
                    "maintainer": "SIG/Core"
                },
                "payload": {
                    "compose": {
                        "date": datestamp,
                        "id": compose_id,
                        "fullname": fullname,
                        "release": release,
                        "timestamp": timestamp
                    }
                }
        }

        with open(file_path + ".json", "w+") as fp:
            json.dump(metadata, fp, indent=4)
            fp.close()

        with open(file_path + ".yaml", "w+") as yp:
            yaml.dump(metadata, yp)
            yp.close()

    @staticmethod
    def discinfo_write(timestamp, fullname, arch, file_path):
        """
        Ensure discinfo is written correctly
        """
        data = [
            "%s" % timestamp,
            "%s" % fullname,
            "%s" % arch,
            "ALL",
            ""
        ]

        with open(file_path, "w+") as f:
            f.write("\n".join(data))
            f.close()

    @staticmethod
    def media_repo_write(timestamp, fullname, file_path):
        """
        Ensure media.repo exists
        """
        data = [
            "[InstallMedia]",
            "name=%s" % fullname,
            "mediaid=%s" % timestamp,
            "metadata_expire=-1",
            "gpgcheck=0",
            "cost=500",
            "",
        ]

        with open(file_path, "w") as f:
            f.write("\n".join(data))

    @staticmethod
    def generate_compose_dirs(
            compose_base,
            shortname,
            version,
            date_stamp,
            logger
    ) -> str:
        """
        Generate compose dirs for full runs
        """
        compose_base_dir = os.path.join(
                compose_base,
                "{}-{}-{}".format(
                    shortname,
                    version,
                    date_stamp
                )
        )
        logger.info('Creating compose directory %s' % compose_base_dir)
        if not os.path.exists(compose_base_dir):
            os.makedirs(compose_base_dir)

        return compose_base_dir

    @staticmethod
    def podman_cmd(logger) -> str:
        """
        This generates the podman run command. This is in the case that we want
        to do reposyncs in parallel as we cannot reasonably run multiple
        instances of dnf reposync on a single system.
        """
        cmd = None
        if os.path.exists("/usr/bin/podman"):
            cmd = "/usr/bin/podman"
        else:
            logger.error('/usr/bin/podman was not found. Good bye.')
            raise SystemExit("\n\n/usr/bin/podman was not found.\n\nPlease "
                    " ensure that you have installed the necessary packages on "
                    " this system. " + Color.BOLD + "Note that docker is not "
                    "supported." + Color.END
            )
        return cmd

    @staticmethod
    def reposync_cmd(logger) -> str:
        """
        This generates the reposync command. We don't support reposync by
        itself and will raise an error.

        :return: The path to the reposync command. If dnf exists, we'll use
        that. Otherwise, fail immediately.
        """
        cmd = None
        if os.path.exists("/usr/bin/dnf"):
            cmd = "/usr/bin/dnf reposync"
        else:
            logger('/usr/bin/dnf was not found. Good bye.')
            raise SystemExit("/usr/bin/dnf was not found. \n\n/usr/bin/reposync "
                    "is not sufficient and you are likely running on an el7 "
                    "system or a grossly modified EL8+ system, " + Color.BOLD +
                    "which tells us that you probably made changes to these tools "
                    "expecting them to work and got to this point." + Color.END)
        return cmd

    @staticmethod
    def git_cmd(logger) -> str:
        """
        This generates the git command. This is when we need to pull down extra
        files or do work from a git repository.
        """
        cmd = None
        if os.path.exists("/usr/bin/git"):
            cmd = "/usr/bin/git"
        else:
            logger.error('/usr/bin/git was not found. Good bye.')
            raise SystemExit("\n\n/usr/bin/git was not found.\n\nPlease "
                    " ensure that you have installed the necessary packages on "
                    " this system. "
            )
        return cmd

    @staticmethod
    def generate_conf(data, logger, dest_path='/var/tmp') -> str:
        """
        Generates the necessary repo conf file for the operation. This repo
        file should be temporary in nature. This will generate a repo file
        with all repos by default. If a repo is chosen for sync, that will be
        the only one synced.

        :param dest_path: The destination where the temporary conf goes
        :param repo: The repo object to create a file for
        """
        fname = os.path.join(
                dest_path,
                "{}-{}-config.repo".format(data.shortname, data.major_version)
        )
        data.log.info('Generating the repo configuration: %s' % fname)

        if data.repo_base_url.startswith("/"):
            logger.error("Local file syncs are not supported.")
            raise SystemExit(Color.BOLD + "Local file syncs are not "
                "supported." + Color.END)

        prehashed = ''
        if data.hashed:
            prehashed = "hashed-"
        # create dest_path
        if not os.path.exists(dest_path):
            os.makedirs(dest_path, exist_ok=True)
        config_file = open(fname, "w+")
        repolist = []
        for repo in data.repos:

            constructed_url = '{}/{}/repo/{}{}/$basearch'.format(
                    data.repo_base_url,
                    data.project_id,
                    prehashed,
                    repo,
            )

            constructed_url_src = '{}/{}/repo/{}{}/src'.format(
                    data.repo_base_url,
                    data.project_id,
                    prehashed,
                    repo,
            )

            repodata = {
                    'name': repo,
                    'baseurl': constructed_url,
                    'srcbaseurl': constructed_url_src,
                    'gpgkey': data.extra_files['git_raw_path'] + data.extra_files['gpg'][data.gpgkey]
            }
            repolist.append(repodata)

        template = data.tmplenv.get_template('repoconfig.tmpl')
        output = template.render(repos=repolist)
        config_file.write(output)

        config_file.close()
        return fname

    @staticmethod
    def quick_sync(src, dest, logger, tmp_dir):
        """
        Does a quick sync from one place to another. This determines the method
        in which will be used. We will look for fpsync and fall back to
        parallel | rsync if that is also available. It will fail if parallel is
        not available.

        Return true or false on completion?
        """

    @staticmethod
    def simple_sync(src, dest):
        """
        This is for simple syncs only, using rsync or copytree.
        """

    @staticmethod
    def fpsync_method(src, dest, tmp_dir):
        """
        Returns a list for the fpsync command
        """
        cmd = '/usr/bin/fpsync'
        rsync_switches = '-av --numeric-ids --no-compress --chown=10004:10005'
        if not os.path.exists(cmd):
            message = 'fpsync not found'
            retval = 1
            return message, retval

        os.makedirs(tmp_dir, exist_ok=True)

        fpsync_cmd = '{} -o "{}" -n 18 -t {} {} {}'.format(
                cmd,
                rsync_switches,
                tmp_dir,
                src,
                dest
        )

        process = subprocess.call(
                shlex.split(fpsync_cmd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
        )

        if process != 0:
            message = 'Syncing (fpsync) failed'
            retval = process
            return message, retval

        if os.path.exists(dest):
            message = 'Syncing (fpsync) succeeded'
            retval = process
        else:
            message = 'Path synced does not seem to exist for some reason.'
            retval = 1

        return message, retval

    @staticmethod
    def rsync_method(src, dest):
        """
        Returns a string for the rsync command plus parallel. Yes, this is a
        hack.
        """
        find_cmd = '/usr/bin/find'
        parallel_cmd = '/usr/bin/parallel'
        rsync_cmd = '/usr/bin/rsync'
        switches = '-av --chown=10004:10005 --progress --relative --human-readable'

        os.makedirs(dest, exist_ok=True)

        return 'Not available', 1

    @staticmethod
    def s3_determine_latest(s3_bucket, release, arches, filetype, logger):
        """
        Using native s3, determine the latest artifacts and return a dict
        """
        temp = []
        data = {}
        s3 = boto3.client('s3')

        try:
            s3.list_objects(Bucket=s3_bucket)['Contents']
        except:
            logger.error(
                        '[' + Color.BOLD + Color.RED + 'FAIL' + Color.END + '] ' +
                        'Cannot access s3 bucket.'
            )
            raise SystemExit()

        for y in s3.list_objects(Bucket=s3_bucket)['Contents']:
            if filetype in y['Key'] and release in y['Key']:
                temp.append(y['Key'])

        for arch in arches:
            temps = []
            for y in temp:
                if arch in y:
                    temps.append(y)
            temps.sort(reverse=True)
            data[arch] = temps[0]

        return data

    @staticmethod
    def s3_download_artifacts(force_download, s3_bucket, source, dest, logger):
        """
        Download the requested artifact(s) via s3
        """
        s3 = boto3.client('s3')
        if os.path.exists(dest):
            if not force_download:
                logger.warn(
                        '[' + Color.BOLD + Color.YELLOW + 'WARN' + Color.END + '] ' +
                        'Artifact at ' + dest + ' already exists'
                )
                return

        logger.info('Downloading ({}) to: {}'.format(source, dest))
        try:
            s3.download_file(
                    Bucket=s3_bucket,
                    Key=source,
                    Filename=dest
            )
        except:
            logger.error('There was an issue downloading from %s' % s3_bucket)

    @staticmethod
    def reqs_determine_latest(s3_bucket_url, release, arches, filetype, logger):
        """
        Using requests, determine the latest artifacts and return a list
        """
        temp = []
        data = {}

        try:
            bucket_data = requests.get(s3_bucket_url)
        except requests.exceptions.RequestException as e:
            logger.error('The s3 bucket http endpoint is inaccessible')
            raise SystemExit(e)

        resp = xmltodict.parse(bucket_data.content)

        for y in resp['ListBucketResult']['Contents']:
            if filetype in y['Key'] and release in y['Key']:
                temp.append(y['Key'])

        for arch in arches:
            temps = []
            for y in temp:
                if arch in y:
                    temps.append(y)
            temps.sort(reverse=True)
            data[arch] = temps[0]

        return data

    @staticmethod
    def reqs_download_artifacts(force_download, s3_bucket_url, source, dest, logger):
        """
        Download the requested artifact(s) via requests only
        """
        if os.path.exists(dest):
            if not force_download:
                logger.warn(
                        '[' + Color.BOLD + Color.YELLOW + 'WARN' + Color.END + '] ' +
                        'Artifact at ' + dest + ' already exists'
                )
                return
        unurl = s3_bucket_url + '/' + source

        logger.info('Downloading ({}) to: {}'.format(source, dest))
        try:
            with requests.get(unurl, allow_redirects=True) as r:
                with open(dest, 'wb') as f:
                    f.write(r.content)
                    f.close()
                r.close()
        except requests.exceptions.RequestException as e:
            logger.error('There was a problem downloading the artifact')
            raise SystemExit(e)
