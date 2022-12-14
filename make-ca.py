#!/usr/bin/python3
# vim:set et sw=4:
#
# make-ca.py - PKI managment script for LFS and others
#
# Copyright (C) 2022 DJ Lucas <dj@linuxfromscratch.org>
# Copyright (C) 2009 Philipp Kern <pkern@debian.org>
# Copyright (C) 2013 Kai Engert <kaie@redhat.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301,
# USA.

import base64
import os
import re
import ssl
import sys
import tempfile
import textwrap
import time
import urllib.request, urllib.parse, urllib.error
import subprocess

objects = []

# Get values from environment
DESTDIR = os.getenv('DESTDIR')
if DESTDIR is None:
  DESTDIR = ''
JAVA_HOME = os.getenv('JAVA_HOME')
if JAVA_HOME is None:
  JAVA_HOME = "/usr"
CERTDATA = "certdata.txt"

# Do not depend on path, provide them in the config file if not correct
CERTUTIL = "/usr/bin/certutil"
KEYTOOL = JAVA_HOME + "/bin/keytool"
MD5SUM = "/usr/bin/md5sum"
OPENSSL = "/usr/bin/openssl"
SED = "/usr/bin/sed"
TRUST = "/usr/bin/trust"

# Destination configuration
CONFIGDIR = "/etc/make-ca"
PKIDIR = DESTDIR + "/etc/pki"
SSLDIR = DESTDIR + "/etc/ssl"
ANCHORDIR = PKIDIR + "/anchors"
ANCHORLIST = PKIDIR + "/anchors.md5sums"
BUNDLEDIR = PKIDIR + "/tls/certs"
CABUNDLE = BUNDLEDIR + "/ca-bundle.crt"
SMBUNDLE = BUNDLEDIR + "/email-ca-bundle.crt"
CSBUNDLE = BUNDLEDIR + "/objsign-ca-bundle.crt"
CERTDIR = SSLDIR + "/certs"
KEYSTORE = PKIDIR + "/tls/java/cacerts"
NSSDB = PKIDIR + "/nssdb"
LOCALDIR = SSLDIR + "/local"
OLDCERTDATA = SSLDIR + "/" + CERTDATA
MOZILLA_CA_ROOT = CONFIGDIR + "/mozilla-ca-root.pem"

# Where/howto download the certdata.txt file
SRCHOST = "hg.mozilla.org"
SRCURL = "https://" + SRCHOST + "/releases/mozilla-release/raw-file/default/security/nss/lib/ckfw/builtins/certdata.txt"
SRCDAT = SRCURL.replace("raw-file", "log")
PROXY = ''

# Create a temporary directory
TMPDIR = tempfile.mkdtemp()
print("Temp Directory is: %s" % (TMPDIR))

# Open the configuration file to override the above, but for now just leave it.
#####FIXME

# Make sure destination directories exist
os.makedirs(ANCHORDIR, mode = 0o755, exist_ok = True)
os.makedirs(BUNDLEDIR, mode = 0o755, exist_ok = True)
os.makedirs(CERTDIR, mode = 0o755, exist_ok = True)
os.makedirs(KEYSTORE, mode = 0o755, exist_ok = True)

# Get hg revision of existing certdata.txt
if os.path.isfile(OLDCERTDATA):
  # Get the currnet revision
  oldsearch = "# Revision:"
  with open(OLDCERTDATA, 'r') as cdold:
    lines = cdold.readlines()
    for line in lines:
      if line.find(oldsearch) != -1:
          oldrev = line.replace('# Revision:', '')
          print("oldver = %s" % (oldrev))
          break
else:
  oldrev = ''

# Create default ssl context with MOZZILA_CA_ROOT as the only CA
context = ssl.create_default_context()
context.load_verify_locations(cafile=MOZILLA_CA_ROOT)

# Get the revision directly from hg
certdatalogdl = urllib.request.urlopen(SRCDAT, data=None, context=context)
certdatalogfn = TMPDIR + "/certdata.txt.log"
certdatalogbytes = certdatalogdl.read()
certdatalogtext = certdatalogbytes.decode('utf-8')
savelogfile = open(certdatalogfn, 'w')
savelogfile.write(certdatalogtext)
savelogfile.close()

newsearch = "<br/>created <i>"
with open(certdatalogfn, 'r') as newlog:
  lines = newlog.readlines()
  for line in lines:
    if line.find(newsearch) != -1:
      newrev = re.sub("<br/>created <i>.*", '', line)
      print("newrev = %s" % (newrev))
      break

# If the match, just use the existing file
if oldrev == newrev:
  print("Existing file %s is up to date. Skipping download." % (OLDCERTDATA))
else:
  print("Downloading updated certdata.txt...")
  certdatadl = urllib.request.urlopen(SRCURL, data=None, context=context)
  certdatafn = TMPDIR + "/certdata.txt"
  certdatabytes = certdatadl.read()
  certdatatext = certdatabytes.decode('utf-8')
  savefile = open(certdatafn, 'w')
  savefile.write("# Revision:" + newrev + "\n" + certdatatext)
  savefile.close()
  if oldrev != '':
    os.rename(OLDCERTDATA, OLDCERTDATA + ".old")
  os.rename(TMPDIR + "/certdata.txt", OLDCERTDATA)


def printable_serial(obj):
  return ".".join([str(x) for x in obj['CKA_SERIAL_NUMBER']])

# Dirty file parser.
in_data, in_multiline, in_obj = False, False, False
field, ftype, value, binval, obj = None, None, None, bytearray(), dict()
for line in open(OLDCERTDATA, 'r'):
    # Ignore the file header.
    if not in_data:
        if line.startswith('BEGINDATA'):
            in_data = True
        continue
    # Ignore comment lines.
    if line.startswith('#'):
        continue
    # Empty lines are significant if we are inside an object.
    if in_obj and len(line.strip()) == 0:
        objects.append(obj)
        obj = dict()
        in_obj = False
        continue
    if len(line.strip()) == 0:
        continue
    if in_multiline:
        if not line.startswith('END'):
            if ftype == 'MULTILINE_OCTAL':
                line = line.strip()
                for i in re.finditer(r'\\([0-3][0-7][0-7])', line):
                    integ = int(i.group(1), 8)
                    binval.extend((integ).to_bytes(1, sys.byteorder))
                obj[field] = binval
            else:
                value += line
                obj[field] = value
            continue
        in_multiline = False
        continue
    if line.startswith('CKA_CLASS'):
        in_obj = True
    line_parts = line.strip().split(' ', 2)
    if len(line_parts) > 2:
        field, ftype = line_parts[0:2]
        value = ' '.join(line_parts[2:])
    elif len(line_parts) == 2:
        field, ftype = line_parts
        value = None
    else:
        raise NotImplementedError('line_parts < 2 not supported.\n' + line)
    if ftype == 'MULTILINE_OCTAL':
        in_multiline = True
        value = ""
        binval = bytearray()
        continue
    obj[field] = value
if len(list(obj.items())) > 0:
    objects.append(obj)

# Build up trust database.
trustmap = dict()
for obj in objects:
    if obj['CKA_CLASS'] != 'CKO_NSS_TRUST':
        continue
    #key = obj['CKA_LABEL'] + printable_serial(obj)
    key = obj['CKA_LABEL']
    trustmap[key] = obj
    #print(" added trust", key)

# Build up cert database.
certmap = dict()
for obj in objects:
    if obj['CKA_CLASS'] != 'CKO_CERTIFICATE':
        continue
    #key = obj['CKA_LABEL'] + printable_serial(obj)
    key = obj['CKA_LABEL']
    certmap[key] = obj
    #print(" added cert", key)

def obj_to_filename(obj):
    label = obj['CKA_LABEL'][1:-1]
    label = label.replace('/', '_')\
        .replace(' ', '_')\
        .replace('(', '=')\
        .replace(')', '=')\
        .replace(',', '_')
    labelbytes = bytearray()
    i = 0
    imax = len(label)
    while i < imax:
        if i < imax-3 and label[i] == '\\' and label[i+1] == 'x':
            labelbytes.extend(bytes.fromhex(label[i+2:i+4]))
            i += 4
            continue
        labelbytes.extend(str.encode(label[i]))
        i = i+1
        continue
    label = labelbytes.decode('utf-8')
    serial = printable_serial(obj)
    return label + ":" + serial

def write_cert_ext_to_file(f, oid, value, public_key):
    f.write("[p11-kit-object-v1]\n")
    f.write("label: ");
    f.write(tobj['CKA_LABEL'])
    f.write("\n")
    f.write("class: x-certificate-extension\n");
    f.write("object-id: " + oid + "\n")
    f.write("value: \"" + value + "\"\n")
    f.write("modifiable: false\n");
    f.write(public_key)

trust_types = {
  "CKA_TRUST_DIGITAL_SIGNATURE": "digital-signature",
  "CKA_TRUST_NON_REPUDIATION": "non-repudiation",
  "CKA_TRUST_KEY_ENCIPHERMENT": "key-encipherment",
  "CKA_TRUST_DATA_ENCIPHERMENT": "data-encipherment",
  "CKA_TRUST_KEY_AGREEMENT": "key-agreement",
  "CKA_TRUST_KEY_CERT_SIGN": "cert-sign",
  "CKA_TRUST_CRL_SIGN": "crl-sign",
  "CKA_TRUST_SERVER_AUTH": "server-auth",
  "CKA_TRUST_CLIENT_AUTH": "client-auth",
  "CKA_TRUST_CODE_SIGNING": "code-signing",
  "CKA_TRUST_EMAIL_PROTECTION": "email-protection",
  "CKA_TRUST_IPSEC_END_SYSTEM": "ipsec-end-system",
  "CKA_TRUST_IPSEC_TUNNEL": "ipsec-tunnel",
  "CKA_TRUST_IPSEC_USER": "ipsec-user",
  "CKA_TRUST_TIME_STAMPING": "time-stamping",
  "CKA_TRUST_STEP_UP_APPROVED": "step-up-approved",
}

legacy_trust_types = {
  "LEGACY_CKA_TRUST_SERVER_AUTH": "server-auth",
  "LEGACY_CKA_TRUST_CODE_SIGNING": "code-signing",
  "LEGACY_CKA_TRUST_EMAIL_PROTECTION": "email-protection",
}

legacy_to_real_trust_types = {
  "LEGACY_CKA_TRUST_SERVER_AUTH": "CKA_TRUST_SERVER_AUTH",
  "LEGACY_CKA_TRUST_CODE_SIGNING": "CKA_TRUST_CODE_SIGNING",
  "LEGACY_CKA_TRUST_EMAIL_PROTECTION": "CKA_TRUST_EMAIL_PROTECTION",
}

openssl_trust = {
  "CKA_TRUST_SERVER_AUTH": "serverAuth",
  "CKA_TRUST_CLIENT_AUTH": "clientAuth",
  "CKA_TRUST_CODE_SIGNING": "codeSigning",
  "CKA_TRUST_EMAIL_PROTECTION": "emailProtection",
}

cert_distrust_types = {
  "CKA_NSS_SERVER_DISTRUST_AFTER": "nss-server-distrust-after",
  "CKA_NSS_EMAIL_DISTRUST_AFTER": "nss-email-distrust-after",
}

# Dump each CA Root to the anohor directory
for tobj in objects:
    if tobj['CKA_CLASS'] == 'CKO_NSS_TRUST':
        #key = tobj['CKA_LABEL'] + printable_serial(tobj)
        key = tobj['CKA_LABEL']
        print("Certificate:  " + key.replace('"', ''))
        trustbits = []
        distrustbits = []
        openssl_trustflags = []
        openssl_distrustflags = []
        legacy_trustbits = []
        legacy_openssl_trustflags = []
        for t in list(trust_types.keys()):
            if t in tobj and tobj[t] == 'CKT_NSS_TRUSTED_DELEGATOR':
                trustbits.append(t)
                if t in openssl_trust:
                    openssl_trustflags.append(openssl_trust[t])
            if t in tobj and tobj[t] == 'CKT_NSS_NOT_TRUSTED':
                distrustbits.append(t)
                if t in openssl_trust:
                    openssl_distrustflags.append(openssl_trust[t])

        for t in list(legacy_trust_types.keys()):
            if t in tobj and tobj[t] == 'CKT_NSS_TRUSTED_DELEGATOR':
                real_t = legacy_to_real_trust_types[t]
                legacy_trustbits.append(real_t)
                if real_t in openssl_trust:
                    legacy_openssl_trustflags.append(openssl_trust[real_t])
            if t in tobj and tobj[t] == 'CKT_NSS_NOT_TRUSTED':
                raise NotImplementedError('legacy distrust not supported.\n' + line)

        fname = obj_to_filename(tobj)
        try:
            obj = certmap[key]
        except:
            obj = None

        # optional debug code, that dumps the parsed input to files
        #fulldump = "dump-" + fname
        #dumpf = open(fulldump, 'w')
        #dumpf.write(str(obj));
        #dumpf.write(str(tobj));
        #dumpf.close();

        is_legacy = 0
        if 'LEGACY_CKA_TRUST_SERVER_AUTH' in tobj or 'LEGACY_CKA_TRUST_EMAIL_PROTECTION' in tobj or 'LEGACY_CKA_TRUST_CODE_SIGNING' in tobj:
            is_legacy = 1
            if obj == None:
                raise NotImplementedError('found legacy trust without certificate.\n' + line)

            legacy_fname = "legacy-default/" + fname + ".crt"
            f = open(legacy_fname, 'w')
            f.write("# alias=%s\n"%tobj['CKA_LABEL'])
            f.write("# trust=" + " ".join(legacy_trustbits) + "\n")
            if legacy_openssl_trustflags:
                f.write("# openssl-trust=" + " ".join(legacy_openssl_trustflags) + "\n")
            f.write("-----BEGIN CERTIFICATE-----\n")
            temp_encoded_b64 = base64.b64encode(obj['CKA_VALUE'])
            temp_wrapped = textwrap.wrap(temp_encoded_b64.decode(), 64)
            f.write("\n".join(temp_wrapped))
            f.write("\n-----END CERTIFICATE-----\n")
            f.close()

            if 'CKA_TRUST_SERVER_AUTH' in tobj or 'CKA_TRUST_EMAIL_PROTECTION' in tobj or 'CKA_TRUST_CODE_SIGNING' in tobj:
                legacy_fname = TMPDIR + "/" + "legacy-disable/" + fname + ".crt"
                f = open(legacy_fname, 'w')
                f.write("# alias=%s\n"%tobj['CKA_LABEL'])
                f.write("# trust=" + " ".join(trustbits) + "\n")
                if openssl_trustflags:
                    f.write("# openssl-trust=" + " ".join(openssl_trustflags) + "\n")
                f.write("-----BEGIN CERTIFICATE-----\n")
                f.write("\n".join(textwrap.wrap(base64.b64encode(obj['CKA_VALUE']), 64)))
                f.write("\n-----END CERTIFICATE-----\n")
                f.close()

            # don't produce p11-kit output for legacy certificates
            continue

        pk = ''
        cert_comment = ''
        if obj != None:
            # must extract the public key from the cert, let's use openssl
            cert_fname = TMPDIR + "/" + "cert-" + fname
            fc = open(cert_fname, 'w')
            fc.write("-----BEGIN CERTIFICATE-----\n")
            temp_encoded_b64 = base64.b64encode(obj['CKA_VALUE'])
            temp_wrapped = textwrap.wrap(temp_encoded_b64.decode(), 64)
            fc.write("\n".join(temp_wrapped))
            fc.write("\n-----END CERTIFICATE-----\n")
            fc.close();
            pk_fname = TMPDIR + "/" + "pubkey-" + fname
            fpkout = open(pk_fname, "w")
            dump_pk_command = [OPENSSL, "x509", "-in", cert_fname, "-noout", "-pubkey"]
            subprocess.call(dump_pk_command, stdout=fpkout)
            fpkout.close()
            with open (pk_fname, "r") as myfile:
                pk=myfile.read()
            # obtain certificate information suitable as a comment
            comment_fname = TMPDIR + "/" + "comment-" + fname
            fcout = open(comment_fname, "w")
            comment_command = [OPENSSL, "x509", "-in", cert_fname, "-noout", "-text"]
            subprocess.call(comment_command, stdout=fcout)
            fcout.close()
            sed_command = [SED, "--in-place", "s/^/#/", comment_fname]
            subprocess.call(sed_command)
            with open (comment_fname, "r", errors = 'replace') as myfile:
                cert_comment=myfile.read()

        fname += ".tmp-p11-kit"
        f = open(fname, 'w')

        if obj != None:
            is_distrusted = False
            has_server_trust = False
            has_email_trust = False
            has_code_trust = False

            if 'CKA_TRUST_SERVER_AUTH' in tobj:
                if tobj['CKA_TRUST_SERVER_AUTH'] == 'CKT_NSS_NOT_TRUSTED':
                    is_distrusted = True
                elif tobj['CKA_TRUST_SERVER_AUTH'] == 'CKT_NSS_TRUSTED_DELEGATOR':
                    has_server_trust = True

            if 'CKA_TRUST_EMAIL_PROTECTION' in tobj:
                if tobj['CKA_TRUST_EMAIL_PROTECTION'] == 'CKT_NSS_NOT_TRUSTED':
                    is_distrusted = True
                elif tobj['CKA_TRUST_EMAIL_PROTECTION'] == 'CKT_NSS_TRUSTED_DELEGATOR':
                    has_email_trust = True

            if 'CKA_TRUST_CODE_SIGNING' in tobj:
                if tobj['CKA_TRUST_CODE_SIGNING'] == 'CKT_NSS_NOT_TRUSTED':
                    is_distrusted = True
                elif tobj['CKA_TRUST_CODE_SIGNING'] == 'CKT_NSS_TRUSTED_DELEGATOR':
                    has_code_trust = True

            if is_distrusted:
                trust_ext_oid = "1.3.6.1.4.1.3319.6.10.1"
                trust_ext_value = "0.%06%0a%2b%06%01%04%01%99w%06%0a%01%04 0%1e%06%08%2b%06%01%05%05%07%03%04%06%08%2b%06%01%05%05%07%03%01%06%08%2b%06%01%05%05%07%03%03"
                write_cert_ext_to_file(f, trust_ext_oid, trust_ext_value, pk)

            trust_ext_oid = "2.5.29.37"
            if has_server_trust:
                if has_email_trust:
                    if has_code_trust:
                        # server + email + code
                        trust_ext_value = "0%2a%06%03U%1d%25%01%01%ff%04 0%1e%06%08%2b%06%01%05%05%07%03%04%06%08%2b%06%01%05%05%07%03%01%06%08%2b%06%01%05%05%07%03%03"
                    else:
                        # server + email
                        trust_ext_value = "0 %06%03U%1d%25%01%01%ff%04%160%14%06%08%2b%06%01%05%05%07%03%04%06%08%2b%06%01%05%05%07%03%01"
                else:
                    if has_code_trust:
                        # server + code
                        trust_ext_value = "0 %06%03U%1d%25%01%01%ff%04%160%14%06%08%2b%06%01%05%05%07%03%01%06%08%2b%06%01%05%05%07%03%03"
                    else:
                        # server
                        trust_ext_value = "0%16%06%03U%1d%25%01%01%ff%04%0c0%0a%06%08%2b%06%01%05%05%07%03%01"
            else:
                if has_email_trust:
                    if has_code_trust:
                        # email + code
                        trust_ext_value = "0 %06%03U%1d%25%01%01%ff%04%160%14%06%08%2b%06%01%05%05%07%03%04%06%08%2b%06%01%05%05%07%03%03"
                    else:
                        # email
                        trust_ext_value = "0%16%06%03U%1d%25%01%01%ff%04%0c0%0a%06%08%2b%06%01%05%05%07%03%04"
                else:
                    if has_code_trust:
                        # code
                        trust_ext_value = "0%16%06%03U%1d%25%01%01%ff%04%0c0%0a%06%08%2b%06%01%05%05%07%03%03"
                    else:
                        # none
                        trust_ext_value = "0%18%06%03U%1d%25%01%01%ff%04%0e0%0c%06%0a%2b%06%01%04%01%99w%06%0a%10"

            # no 2.5.29.37 for neutral certificates
            if (is_distrusted or has_server_trust or has_email_trust or has_code_trust):
                write_cert_ext_to_file(f, trust_ext_oid, trust_ext_value, pk)

            pk = ''
            f.write("\n")

            f.write("[p11-kit-object-v1]\n")
            f.write("label: ");
            f.write(tobj['CKA_LABEL'])
            f.write("\n")
            if is_distrusted:
                f.write("x-distrusted: true\n")
            elif has_server_trust or has_email_trust or has_code_trust:
                f.write("trusted: true\n")
            else:
                f.write("trusted: false\n")

            # requires p11-kit >= 0.23.4
            f.write("nss-mozilla-ca-policy: true\n")
            f.write("modifiable: false\n");

            # requires p11-kit >= 0.23.19
            for t in list(cert_distrust_types.keys()):
                if t in obj:
                    value = obj[t]
                    if value == 'CK_FALSE':
                        value = bytearray(1)
                    f.write(cert_distrust_types[t] + ": \"")
                    f.write(urllib.parse.quote(value));
                    f.write("\"\n")

            f.write("-----BEGIN CERTIFICATE-----\n")
            temp_encoded_b64 = base64.b64encode(obj['CKA_VALUE'])
            temp_wrapped = textwrap.wrap(temp_encoded_b64.decode(), 64)
            f.write("\n".join(temp_wrapped))
            f.write("\n-----END CERTIFICATE-----\n")
            f.write(cert_comment)
            f.write("\n")

        else:
            f.write("[p11-kit-object-v1]\n")
            f.write("label: ");
            f.write(tobj['CKA_LABEL']);
            f.write("\n")
            f.write("class: certificate\n")
            f.write("certificate-type: x-509\n")
            f.write("modifiable: false\n");
            f.write("issuer: \"");
            f.write(urllib.parse.quote(tobj['CKA_ISSUER']));
            f.write("\"\n")
            f.write("serial-number: \"");
            f.write(urllib.parse.quote(tobj['CKA_SERIAL_NUMBER']));
            f.write("\"\n")
            if (tobj['CKA_TRUST_SERVER_AUTH'] == 'CKT_NSS_NOT_TRUSTED') or (tobj['CKA_TRUST_EMAIL_PROTECTION'] == 'CKT_NSS_NOT_TRUSTED') or (tobj['CKA_TRUST_CODE_SIGNING'] == 'CKT_NSS_NOT_TRUSTED'):
              f.write("x-distrusted: true\n")
            f.write("\n\n")
        f.close()
        os.remove(cert_fname)
        os.remove(comment_fname)
        os.remove(pk_fname)
        fhash_command = [OPENSSL, "x509", "-in", fname, "-hash", "--nocert"]
        fhash = subprocess.run(fhash_command, stdout=subprocess.PIPE).stdout.decode('utf-8').strip()
        nfname = ANCHORDIR + "/" + fhash
        nfname += ".p11-kit"
        os.rename(fname,nfname)
        print("Keyhash:      %s" % (fhash))
        t   = ', '.join(trustbits)
        ot  = ', '.join(openssl_trustflags)
        dt  = ', '.join(distrustbits)
        odt = ', '.join(openssl_distrustflags)
        print("Added to p11-kit anchor directory with the following trust bits:\n  Mozilla Trust:     %s\n  OpenSSL Trust:     %s\n  Mozilla Distrust:  %s\n  OpenSSL Distrust:  %s\n\n" % (t, ot, dt, odt))

# Create OpenSSL trust store
print("Extracting OpenSSL certificates to:\n%s..." % (CERTDIR), end='')
create_OTS = [TRUST, "extract", "--filter=ca-anchors", "--format=openssl-directory", "--overwrite", "--comment", CERTDIR]
subprocess.run(create_OTS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("Done!")

# Create GNUTLS bundle file
print("Extracting GNUTLS server auth certificates to:\n%s..." % (CABUNDLE), end='')
create_GBF = [TRUST, "extract", "--filter=ca-anchors", "--format=pem-bundle", "--purpose", "server-auth", "--overwrite", "--comment", CABUNDLE]
subprocess.run(create_GBF, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("Done!")

# Create GNUTLS S-Mime bundle
print("Extracting GNUTLS S-Mime certificates to:\n%s..." % (SMBUNDLE), end='')
create_GSM = [TRUST, "extract", "--filter=ca-anchors", "--format=pem-bundle", "--purpose", "email", "--overwrite", "--comment", SMBUNDLE]
subprocess.run(create_GSM, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("Done!")

# Create GNUTLS Code Signing bundle
print("Extracting GNUTLS code signing certificates to:\n%s..." % (CSBUNDLE), end='')
create_GCS = [TRUST, "extract", "--filter=ca-anchors", "--format=pem-bundle", "--purpose", "code-signing", "--overwrite", "--comment", CSBUNDLE]
subprocess.run(create_GCS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("Done!")

# Create Java certificate store
print("Extracting Java cacerts (JKS) to:\n%s..." % (KEYSTORE), end='')
create_JKS = [TRUST, "extract", "--filter=ca-anchors", "--format=java-cacerts", "--purpose", "server-auth", "--overwrite", "--comment", KEYSTORE]
subprocess.run(create_JKS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("Done!")

