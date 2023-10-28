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
  
default_workbook = 'plant-humanities'
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

ignore = ['juncture', '.venv', 'media']
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

def find_media(path):
  images = []
  md = open(path, 'r').read()
  html = markdown.markdown(md, extensions=['extra', 'toc'])
  soup = BeautifulSoup(html, 'html5lib')
  for param in soup.find_all('param'):
    image = None
    page = path.replace(f'{BASEDIR}/', '').replace('/README.md', '')
    if 've-config' in param.attrs and 'banner' in param.attrs:
      image = {'page': page, 'url': param.attrs['banner']}
    elif 've-image' in param.attrs:
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
  if 'items' in obj and isinstance(obj['items'], list):
    for item in obj['items']:
      if item.get('type') == type and (attr is None or item.get(attr) == attr_val):
          return item[sub_attr] if sub_attr else item
      return _find_item(item, type, attr, attr_val, sub_attr)

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
          elif label not in ('acct', 'repo', 'essay', 'ref'):
            props[label] = value
    if 'label' in img:
      props['label'] = img['label']
    if 'attribution' in img:
      props['requiredStatement'] = {'attribution': value}
    if 'license' in img:
      props['license'] = img['license']
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
    for img in find_media(path):
      src = None
      manifest = None
      if 'url' in img:
        url = img['url']
        if not url.startswith('http'):
          src = url[1:] if url.startswith('/') else f'{page}/{url}'
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
        manifest = get_manifest(img['manifest'])
        if img['manifest'].startswith('https://iiif.juncture-digital.org'):
          image_data = _find_item(manifest, type='Annotation', attr='motivation', attr_val='painting', sub_attr='body')
          url = image_data['id']
          if 'jstor-labs/plant-humanities' in url.lower():
            src = '/'.join(pe for pe in url.replace('?raw=true','').split('/') if pe and pe.lower() not in('https:', 'github.com', 'raw.githubusercontent.com', '52bc9a2', 'blob', 'master', 'main', 'raw', 'develop', 'staging-3', 'staging-7', 'jstor-labs', 'plant-humanities'))
      else:
        continue
      
      props = manifest_props(img, manifest) if manifest else {}
      props['hash'] = hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]

      if src:
        src_path = f'{essays}/{src}'
        dst = urllib.parse.unquote(src).split('/')
        dst[-1] = dst[-1].replace(' ', '_')
        for i in range(len(dst)-1):
          dst[i] = dst[i].lower().replace(' ', '-').replace('_', '-')
        dst_path = f'{media}/{"/".join(dst)}'
        
  
        if os.path.exists(src_path):
          if not dryrun:
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copyfile(src_path, dst_path)

        yaml_path, _ = os.path.splitext(dst_path)
        logger.info(f'+ {yaml_path}.yaml')
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
          logger.info(f'-> {path}')
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