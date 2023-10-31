#!/usr/bin/env python
# -*- coding: utf-8 -*-

# dependencies: gspread oauth2client requests

import logging
logging.basicConfig(format='%(asctime)s : %(levelname)s :  %(name)s : %(message)s')
logger = logging.getLogger()
logger.setLevel(logging.WARNING)

import os
SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
BASEDIR = os.path.abspath(os.path.dirname(SCRIPT_DIR))

import argparse, json, shutil, yaml
import urllib.parse
from shutil import copy2

import hashlib

import markdown
from bs4 import BeautifulSoup

import requests
logging.getLogger('requests').setLevel(logging.INFO)

import zlib, sqlite3
from sqlitedict import SqliteDict
def cache_encode(obj):
    return sqlite3.Binary(zlib.compress(json.dumps(obj).encode('utf-8')))
def cache_decode(obj):
    return json.loads(zlib.decompress(bytes(obj)).decode('utf-8'))
cache = SqliteDict(f'{SCRIPT_DIR}/manifests.sqlite', encode=cache_encode, decode=cache_decode, autocommit=True)

if os.path.exists(f'{SCRIPT_DIR}/names_map.tsv'):
  with open(f'{SCRIPT_DIR}/names_map.tsv', 'r') as f:
    names_map = {line.split('\t')[0]: line.split('\t')[1].strip() for line in f.readlines()}
else:
  names_map = {}
  
default_workbook = 'Plant Humanities'
default_worksheet = 'media'

import gspread
from gspread.cell import Cell
from oauth2client.service_account import ServiceAccountCredentials

logging.getLogger('oauth2client.client').setLevel(logging.WARNING)
scope = ['https://spreadsheets.google.com/feeds',
         'https://www.googleapis.com/auth/drive']

def get_workbook(workbook=default_workbook, **kwargs):
  creds_file = f'{SCRIPT_DIR}/gcreds.json'
  creds = ServiceAccountCredentials.from_json_keyfile_name(creds_file, scope)
  client = gspread.authorize(creds)
  return client.open(workbook)

def as_hyperlink(url, label=None):
  return f'=HYPERLINK("{url}", "{label}")'

def as_image(url):
  return f'=IMAGE("{url}")'

ignore = ['juncture', '.venv', 'media', 'John']
def list_pages(base=BASEDIR):
  pages = []
  for root, _, files in os.walk(base):
    for file in files:
      if file == 'README.md':
        page_path = root.replace(base, '').split('/')
        page = page_path[1] if len(page_path) > 1 else 'root'
        if page in ignore: continue
        pages.append(root.replace(base, '')[1:])
  return sorted(pages)

def find_media(path, md=None):
  images = []
  md = md or open(path, 'r').read()
  html = markdown.markdown(md, extensions=['extra', 'toc'])
  soup = BeautifulSoup(html, 'html5lib')
  for param in soup.find_all('param'):
    image = None
    page = path.replace(f'{BASEDIR}/', '').replace('/README.md', '')
    if 've-config' in param.attrs and 'banner' in param.attrs:
      image = {'page': page, 'url': param.attrs['banner']}
    elif 've-image' in param.attrs or 've-compare' in param.attrs:
      if 'url' in param.attrs or 'manifest' in param.attrs:
        image = {**{'page': page}, **dict([(k, v) for k, v in param.attrs.items() if k not in ['ve-image']])}
    
    if image:
      images.append(image)
  
  return images

def transform_media_path(src_img):
  src_fname = src_img.split('/')[-1]
  if src_fname not in names_map:
    names_map[src_fname] = src_fname
  path = [pe for pe in src_img.split('/') if pe and pe != 'media'][:-1]
  return f'{"/".join(path)}/{names_map[src_fname]}'

def move_image(src, dst, image, dryrun=False, **kwargs):
  logger.info(f'{src} -> {dst}')
  if not dryrun:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst, copy_function = copy2)
    props = {}
    if 'label' in image: props['label'] = image['label']
    if 'description' in image: props['summary'] = image['description']
    if 'summary' in image: props['summary'] = image['summary']
    if 'license' in image: props['rights'] = image['license']
    if 'rights' in image: props['rights'] = image['rights']
    if 'attribution' in image: props['requiredStatement'] = {'label': 'attribution', 'value': image['attribution']}
    if props:
      yaml_path, _ = os.path.splitext(dst)
      yaml.dump(props, open(f'{yaml_path}.yaml', 'w'), default_flow_style=False)
      logger.info(f'+ {yaml_path}.yaml')

def _find_item(obj, type, attr=None, attr_val=None, sub_attr=None):
  try:
    if 'items' in obj and isinstance(obj['items'], list):
      for item in obj['items']:
        if item.get('type') == type and (attr is None or item.get(attr) == attr_val):
            return item[sub_attr] if sub_attr else item
        return _find_item(item, type, attr, attr_val, sub_attr)
  except:
    pass

def get_manifest(url):
  manifest = cache.get(url)
  if not manifest:
    resp = requests.get(url)
    if resp.status_code != 200:
      logger.error(f'get_manifest: {url} {resp.status_code}')
      return None
    try:
      manifest = resp.json()
      if manifest.get('@context') == 'http://iiif.io/api/presentation/2/context.json':
        manifest = requests.get(f'https://iiif.juncture-digital.org/prezi2to3/?manifest={url}').json()
      cache[url] = manifest
    except:
      pass
  return manifest

def manifest_props(img, manifest):
  props = {}
  if manifest:
    try:
      props['label'] = manifest['label']['en'][0]
      if 'summary' in manifest: props['summary'] = manifest['summary']['en'][0]
      if 'requiredStatement' in manifest:
        label = manifest['requiredStatement']['label']['en'][0] or 'attribution'
        value = manifest['requiredStatement']['value']['en'][0]
        props['requiredStatement'] = {label: value}
      if 'metadata' in manifest:
        for md in manifest['metadata']:
          label = md['label']['en'][0].lower()
          value = md['value']['en'][0]
          if label and value:
            if label == 'description':
              if 'summary' not in props:
                props['summary'] = value
            elif label == 'attribution':
              if 'requiredStatement' not in props:
                props['requiredStatement'] = {label: value}
            elif label == 'attribution-url':
              if 'requiredStatement' in props:
                props['requiredStatement']['url'] = value
              else:
                props['requiredStatement'] = {'url': value}
            elif label == 'license':
              props['sourceLicense'] = value
              license = value
              if 'Public domain' in license or 'Not in copyright' in license or 'PDM' in license:
                props['reuseRights'] = 'https://creativecommons.org/publicdomain/mark/1.0'
              elif 'CC0' in license: props['reuseRights'] = 'https://creativecommons.org/publicdomain/zero/1.0'
              elif 'CC BY 2.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by/4.0'
              elif 'CC BY 4.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by/4.0'
              elif 'CC BY-SA 2.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by-sa/4.0'
              elif 'CC-BY-SA-4.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by-sa/4.0'
              elif 'CC BY-NC-SA' in license or 'Attribution, Non-Commercial, ShareAlike' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by-nc-sa/4.0/deed.en'
              elif 'CC BY-NC-SA 2.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by-nc-sa/4.0/deed.en'
              elif 'CC BY-NC-SA 4.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by-nc-sa/4.0/deed.en'
              elif 'CC BY-NC 3.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by-nc/4.0/deed.en'
              elif 'CC BY-NC 4.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by-nc/4.0/deed.en'
              elif 'CC BY-NC-ND 2.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by-nc-nd/4.0/deed.en'
              elif 'CC BY-NC-ND 3.0' in license: props['reuseRights'] = 'https://creativecommons.org/licenses/by-nc-nd/4.0/deed.en'
            elif label not in ('acct', 'repo', 'essay', 'ref', 'image-source-url'):
              props[label] = value
    except:
      # logger.info(json.dumps(manifest, indent=2))
      raise
  if 'label' in img:
    props['label'] = img['label']
  if 'attribution' in img:
    props['requiredStatement'] = {'attribution': value}

  if 'license' in img:
    props['sourceLicense'] = img['license']
  if 'source' in img:
    props['source'] = img['source']
  if 'author' in img:
    props['author'] = img['author']
  return props

def sync_media(essays, media, max=-1, dryrun=False, **kwargs):
  logger.info(f'sync_media: essays={essays} media={media} dryrun={dryrun}')
  names_map_updated = False
  for page in list_pages(essays):
    # logger.info(f'page={page}')
    path = f'{essays}/{page}/README.md' if page else f'{essays}/README.md'
    md = open(path, 'r').read()
    md_updated = False
    num_updated = 0
    for img in find_media(path, md):
      src = None
      manifest = None
      props = {}
      if 'url' in img:
        url = img['url']
        fname = urllib.parse.unquote(url.split('/')[-1]).replace(' ', '_')
        if not url.startswith('http'):
          src = url[1:] if url.startswith('/') else f'{page}/{url}'
          gh_url = f'https://raw.githubusercontent.com/plant-humanities/media/main/{src}'
          md = md.replace(url, gh_url)
          md_updated = True
        elif 'jstor-labs/plant-humanities' in img['url'].lower():
          src = '/'.join(pe for pe in url.replace('?raw=true','').split('/') if pe and pe.lower() not in('https:', 'github.com', 'raw.githubusercontent.com', '52bc9a2', 'blob', 'master', 'main', 'develop', 'raw', 'staging-3', 'staging-7', 'jstor-labs', 'plant-humanities'))
        else:
          continue

        '''
        src_img = img['url']
        src_fname = src_img.split('/')[-1]
        if src_fname not in names_map:
          names_map_updated = True
          names_map[src_fname] = src_fname
        dst_img_path = transform_media_path(src_img)
        src = f'{essays}{src_img}'
        dst = f'{media}/{dst_img_path}'
        img_url = f'https://raw.githubusercontent.com/plant-humanities/media/main/{dst_img_path}'
        md = md.replace(src_img, img_url)
        md_updated = True
        if os.path.exists(src) and not os.path.exists(dst):
          logger.debug(f'{src} ({os.path.exists(src)}) {dst} ({os.path.exists(dst)}) {img_url}')
          move_image(src, dst, img, dryrun=dryrun, **kwargs)
        '''

      elif 'manifest' in img:
        manifest_url = img['manifest']
        if manifest_url.startswith('https://iiif.juncture-digital.org'):
          manifest = get_manifest(manifest_url)
          image_data = _find_item(manifest, type='Annotation', attr='motivation', attr_val='painting', sub_attr='body')
          if image_data and 'id' in image_data:
            url = image_data['id']
            m_name, ext = os.path.splitext( urllib.parse.unquote(url.split('/')[-1]).replace(' ', '_'))
            # props = manifest_props(img, manifest) if manifest else {}
            props['hash'] = hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]
            if 'jstor-labs' in url.lower() and 'plant-humanities' in url.lower():
              parts = [pe for pe in url.replace('?raw=true','').split('/') if pe and pe.lower() not in('https:', 'jstor-labs.github.io', 'github.com', 'raw.githubusercontent.com', '52bc9a2', 'blob', 'master', 'main', 'raw', 'develop', 'staging-3', 'staging-7', 'jstor-labs', 'plant-humanities')]
              # logger.info(parts)
              src = urllib.parse.unquote('/'.join(parts))
              parts[0] = parts[0].replace(' ', '-').replace('_', '-')
              m_name = f'gh:plant-humanities/media/{"/".join(parts)}'
            else:
              if url.startswith('https://upload.wikimedia.org'):
                props = {}
                parts = url.split('/')
                wc_file = parts[8] if parts[5] == 'thumb' else parts[-1]
                m_name = f'wc:{wc_file}'
              else:
                props['image_url'] = url
                name_hash = hashlib.sha256(m_name.encode("utf-8")).hexdigest()
                m_name = f'gh:plant-humanities/media/{page.lower()}/{name_hash}.yaml'
                src = f'{page.lower()}/{name_hash}.yaml'
            src_path = f'{essays}/{urllib.parse.unquote(src)}' if src else None
            # if src_path: logger.info(f'{src_path} ({os.path.exists(src_path)})')
            m_name = urllib.parse.unquote(m_name)
            m_name = urllib.parse.quote(m_name)
            manifest_short = f'https://iiif.juncture-digital.org/{m_name}/manifest.json'
            logger.info(manifest_short)
            md = md.replace(manifest_url, manifest_short)
            md_updated = True
            # logger.info(manifest_short)
      else:
        continue
      
      if src:
        src_path = f'{essays}/{src}'
        dst = urllib.parse.unquote(src).split('/')
        dst[-1] = dst[-1].replace(' ', '_')
        for i in range(len(dst)-1):
          dst[i] = dst[i].lower().replace(' ', '-').replace('_', '-')
        dst_path = f'{media}/{"/".join(dst)}'
        
        # logger.info(f'{src_path} ({os.path.exists(src_path)}) -> {dst_path}')
        if os.path.exists(src_path) and os.path.isfile(src_path):
          if not dryrun:
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copyfile(src_path, dst_path)
            os.remove(src_path)
            if props:
              yaml_path, _ = os.path.splitext(dst_path)        
              with open(f'{yaml_path}.yaml', 'w') as f:
                f.write(yaml.safe_dump(props, sort_keys=False, width=float('inf')))
      
      if props and 'image_url' in props:
        yaml_path, _ = os.path.splitext(dst_path)        
        os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
        with open(f'{yaml_path}.yaml', 'w') as f:
          f.write(yaml.safe_dump(props, sort_keys=False, width=float('inf')))
        
    '''
    if md_updated:
      num_updated += 1
      if dryrun:
        pass # print(md)
      else:
        with open(path, 'w') as f:
          f.write(md)
          # logger.info(f'-> {path}')
      if num_updated == max: break
    '''
  
  '''
  if names_map_updated:
    with open(f'{SCRIPT_DIR}/names_map.tsv', 'w') as f:
      for src, dst in names_map.items():
        f.write(f'{src}\t{dst}\n')
  '''

def check_images(essays, **kwargs):
  logger.info(f'check_images: essays={essays}')
  for page in list_pages(essays):
    path = f'{essays}/{page}/README.md' if page else f'{essays}/README.md'
    for img in find_media(path):
      if 'url' not in img: continue
      if img['url'].startswith('https://raw.githubusercontent.com/plant-humanities/media/main/'):
        found = requests.head(img['url']).status_code == 200
        if not found:
          logger.info(f'{page} {img["url"]}')

def inventory_images(essays, dryrun=False, **kwargs):
  logger.info(f'inventory_images: essays={essays}')
  fields = ['Essay', 'Thumbnail', 'Manifest', 'Image', 'Acct', 'Repo', 'Branch']
  row = 2
  ws_updates = []
  for page in list_pages(essays):
    path = f'{essays}/{page}/README.md' if page else f'{essays}/README.md'
    md = open(path, 'r').read()
    for img in find_media(path, md):
      if 'url' in img:
        if not img['url'].startswith('http'):
          if img['url'].startswith('/'):
            # img['url'] = f'https://github.com/JSTOR-Labs/plant-humanities/blob/main/{img["url"][1:]}?raw=true'
            img['url'] = f'https://raw.githubusercontent.com/JSTOR-Labs/plant-humanities/main/{img["url"][1:]}'
          else:
            # img['url'] = f'https://github.com/JSTOR-Labs/plant-humanities/blob/main/{page}/{img["url"]}?raw=true'
            img['url'] = f'https://raw.githubusercontent.com/JSTOR-Labs/plant-humanities/main/{page}/{img["url"]}'
      if 'manifest' in img:
        if img['manifest'].startswith('https://iiif.juncture-digital.org'):
          manifest = get_manifest(img['manifest'])
          if manifest:
            image_data = _find_item(manifest, type='Annotation', attr='motivation', attr_val='painting', sub_attr='body')
            if image_data and 'id' in image_data:
              img['url'] = image_data['id']
      
      if dryrun:
        print(f'{page}\t{img["manifest"] if "manifest" in img else ""}\t{img.get("url","Not found")}')
      else:
        row += 1
        row_data = { 'Essay': as_hyperlink(f'https://beta.plant-humanities.org/{page}', page or 'Home') }
        if 'manifest' in img:
          m = urllib.parse.urlparse(img['manifest'])
          if m.hostname == 'iiif.juncture-digital.org':
            row_data['Manifest'] = as_hyperlink(f'https://iiif.juncture-digital.org#{img["manifest"]}', 'Juncture')
          elif m.hostname.endswith('harvard.edu'):
            row_data['Manifest'] = as_hyperlink(img['manifest'], 'Harvard')
          elif m.hostname.endswith('loc.gov'):
            row_data['Manifest'] = as_hyperlink(img['manifest'], 'Library of Congress')
          else:
            logger.info(m.hostname)
            row_data['Manifest'] = as_hyperlink(img['manifest'], m.hostname)
        if 'url' in img:
          u = urllib.parse.urlparse(img['url'])
          if u.hostname == 'raw.githubusercontent.com':
            [acct, repo, branch] = u.path.split('/')[1:4]
            row_data['Acct'] = acct
            row_data['Repo'] = repo
            row_data['Branch'] = branch
            row_data['Image'] = as_hyperlink(img['url'], 'Github')
          elif u.hostname == 'github.com':
            [acct, repo, _, branch] = u.path.split('/')[1:5]
            row_data['Acct'] = acct
            row_data['Repo'] = repo
            row_data['Branch'] = branch
            row_data['Image'] = as_hyperlink(img['url'], 'Github')
          elif u.hostname == 'jstor-labs.github.io':
            row_data['Acct'] = 'JSTOR-Labs'
            row_data['Repo'] = u.path.split('/')[2]
            row_data['Branch'] = 'main'
            row_data['Image'] = as_hyperlink(img['url'], 'Github')
          elif u.hostname == 'upload.wikimedia.org':
            row_data['Image'] = as_hyperlink(img['url'], 'Wikimedia')
          elif u.hostname == 'live.staticflickr.com':
            row_data['Image'] = as_hyperlink(img['url'], 'Flickr')            
          elif u.hostname.endswith('biodiversitylibrary.org'):
            row_data['Image'] = as_hyperlink(img['url'], 'BHL')
          elif u.hostname == 'www.doaks.org':
            row_data['Image'] = as_hyperlink(img['url'], 'Dumbarton Oaks')
          elif u.hostname.endswith('loc.gov'):
            row_data['Image'] = as_hyperlink(img['url'], 'Library of Congress')
          elif u.hostname.endswith('wellcomecollection.org'):
            row_data['Image'] = as_hyperlink(img['url'], 'Wellcome Collection')
          elif u.hostname.endswith('harvard.edu'):
            row_data['Image'] = as_hyperlink(img['url'], 'Harvard')
          elif u.hostname.endswith('archive.org'):
            row_data['Image'] = as_hyperlink(img['url'], 'Internet Archive')
          elif u.hostname.endswith('metmuseum.org'):
            row_data['Image'] = as_hyperlink(img['url'], 'The MET')
          else:
            row_data['Image'] = as_hyperlink(img['url'], u.hostname)
          row_data['Thumbnail'] = as_image(f'https://iiif.juncture-digital.org/thumbnail/?url={img["url"]}')
        else:
          row_data['Image'] = ''
        ws_updates += [Cell(row, fields.index(fld) + 1, val) for fld, val in row_data.items()]

  if ws_updates:
    wb = get_workbook(**kwargs)
    ws = wb.worksheet(kwargs.get('worksheet', default_worksheet))
    ws_updates.sort(key=lambda cell: cell.col, reverse=False)
    ws.update_cells(ws_updates, value_input_option='USER_ENTERED')
      

def update_google_sheets(**kwargs):
  fields = {
    'Page': 0,
    'Thumbnail': 1,
    'URL': 2
  }
  wb = get_workbook(**kwargs)
  ws = wb.worksheet(kwargs.get('worksheet', default_worksheet))
  ws_updates = []
  
  row = 0
  for page in list_pages():
    for img in find_media(page):
      if 'url' not in img: continue
      row += 1
      row_data = {
        'Page': as_hyperlink(f'https://beta.plant-humanities.org/{page}', page or 'Home'),
        'URL': as_hyperlink(img['url'], img['url'].split('/')[-1]),
      }
    ws_updates += [Cell(row, fields[fld] + 1, val) for fld, val in row_data.items() if fld in fields]

  if ws_updates:
    ws_updates.sort(key=lambda cell: cell.col, reverse=False)
    ws.update_cells(ws_updates, value_input_option='USER_ENTERED')

if __name__ == '__main__':
  parser = argparse.ArgumentParser(description='Images admin tool')
  parser.add_argument('--debug', type=bool, default=False, action=argparse.BooleanOptionalAction, help='Generate debug output')
  parser.add_argument('--quiet', type=bool, default=False, action=argparse.BooleanOptionalAction, help='Disable logging')
  parser.add_argument('--sync', type=bool, default=False, action=argparse.BooleanOptionalAction, help='Sync images with essays')
  parser.add_argument('--check', type=bool, default=False, action=argparse.BooleanOptionalAction, help='Check images')
  parser.add_argument('--inventory', type=bool, default=False, action=argparse.BooleanOptionalAction, help='Inventory images')
  parser.add_argument('--max', type=int, default=-1, help='Maximum essays to process')
  parser.add_argument('--dryrun', type=bool, default=False, action=argparse.BooleanOptionalAction, help='Do dryrun')
  parser.add_argument('--essays', type=str, default=BASEDIR, help='Essays root directory')
  parser.add_argument('--media', type=str, default=os.path.abspath(f'{BASEDIR}/../media'), help='Media root directory')
  parser.add_argument('--workbook', type=str, default=default_workbook, help='Google sheets workbook')
  parser.add_argument('--worksheet', type=str, default=default_worksheet, help='Google sheets worksheet')
  args = vars(parser.parse_args())

  if args['debug']: logger.setLevel(logging.DEBUG)
  elif not args['quiet']: logger.setLevel(logging.INFO)
  args.pop('debug')
  args.pop('quiet')
    
  logger.debug(json.dumps(args, indent=2))

  if args['sync']:
    sync_media(**args)
  elif args['check']:
    check_images(**args)
  elif args['inventory']:
    inventory_images(**args)