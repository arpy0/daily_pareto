# pylint: disable=invalid-name
# pylint: disable=missing-function-docstring
# pylint: disable=missing-module-docstring
# pylint: disable=unspecified-encoding

import os, json, re
import dropbox
import discord
from discord import app_commands
from datetime import datetime,timedelta
from pprint import pformat

import matplotlib.ticker as mticker
import matplotlib.pyplot as pl

import numpy as np
import requests

DISCORD_BOT_TOKEN = os.environ['DISCORD_BOT_TOKEN']
DISCORD_GUILD = os.environ['DISCORD_GUILD']
DISCORD_CHANNEL = os.environ['DISCORD_CHANNEL']
DISCORD_THREAD_1 = os.environ['DISCORD_THREAD_1']
DISCORD_THREAD_2 = os.environ['DISCORD_THREAD_2']
DISCORD_LEADERBOARD_BOT = os.environ['DISCORD_LEADERBOARD_BOT']
DROPBOX_APP_KEY = os.environ['DROPBOX_APP_KEY']
DROPBOX_APP_SECRET = os.environ['DROPBOX_APP_SECRET']
DROPBOX_ACCESS_TOKEN = os.environ['DROPBOX_ACCESS_TOKEN']
DROPBOX_REFRESH_TOKEN = os.environ['DROPBOX_REFRESH_TOKEN']

intents = discord.Intents(dm_messages=True, messages=True, message_content=True, members=True, guilds=True)
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

dbx = dropbox.Dropbox(oauth2_access_token=DROPBOX_ACCESS_TOKEN,oauth2_refresh_token=DROPBOX_REFRESH_TOKEN,
                      app_key=DROPBOX_APP_KEY,app_secret=DROPBOX_APP_SECRET)

def normed(x):
  return x / np.sum(x)

def pareto_compare(a, b) -> bool:
  # return True if a is beat by b
  if a == b:
    return False
  return all(z[0] >= z[1] for z in zip(a, b))

def score_string(scores, category) -> str:
  return ', '.join(['/'.join([''.join(z) for z in zip(map(str, s), category.lower())]) for s in scores])

class Pick():
  def __init__(self, ordinal, reroll=False):
    # fyi, ordinal is the number of days since 0001-01-01. I use it to uniquely identify each day
    self.day = ordinal
    if reroll or not self.load():
      self.start_scores = self.random()
    self.end_scores = self.get_frontier()
    self.save()

  def __repr__(self):
    return pformat(self.__dict__)

  def random(self):
    # maybe consider using a seeded random base on date somehow.  it gets messy if you have to reroll though
    rng = np.random.default_rng()

    self.puzzle = rng.choice(
      rng.choice(grouped_puzzles, p=grouped_puzzles_weight))
    scores = None
    while not scores:
      # here we're continue rolling category until we get a good one
      # TODO: don't pick more than 1 of AHW
      # also, probably ban CR.  all kinds of filtering should be considered

      probs = normed(categories_weight[self.puzzle['type'] == 'PRODUCTION'])
      self.category = ''.join(rng.choice(categories, 3, False, p=probs))
      self.category, self.min_flag = ''.join(sorted(self.category[:2])), self.category[2]
      self.flags = ''.join(
        (rng.choice(['', 'O'], p=[0.99,0.01]),
         rng.choice(['', 'T'], p=[0.9, 0.1]),
         rng.choice(['', self.min_flag], p=[0.95, 0.05])))

      # gets the scores.  if there is ever a problem with the category, it will return None which forces this function to reroll category
      scores = self.get_frontier()
    return scores

  def load(self) -> bool:
    try:
      data = dbx.files_download(f"/picks/{self.day}.txt")[1]
      data_json = json.loads(data.content)
      for i in data_json:
        self.__dict__[i] = data_json[i]
      return True
    except dropbox.exceptions.ApiError:
      return False

  def save(self):
    dbx.files_upload(bytes(json.dumps(self.__dict__,indent=4),'utf-8'),f"/picks/{self.day}.txt",
                    mode=dropbox.files.WriteMode.overwrite)

  def get_frontier(self):
    # todo: maybe should be caching the results incase of a category reroll
    url = f'https://zlbb.faendir.com/om/puzzle/{self.puzzle["id"]}/records?includeFrontier=true'
    frontier = requests.get(url).json()
    for solution in frontier:
      if solution['score']['rate'] in (None, 'Infinity'):
        solution['score']['rate'] = np.inf

    if 'O' not in self.flags:
      frontier = tuple(
        filter(lambda x: x['score']['overlap'] == False, frontier))
    if 'T' in self.flags:
      frontier = tuple(
        filter(lambda x: x['score']['trackless'] == True, frontier))
    try:
      if self.min_flag in self.flags:
        self.min_score = min(i['score'][catmap[self.min_flag]]
                             for i in frontier)
        frontier = tuple(
          filter(lambda x: x['score'][catmap[self.min_flag]] == self.min_score,
                 frontier))
    except AttributeError:
      pass

    # pull out the scored metrics for today
    scores = [tuple(f['score'][catmap[c]] for c in self.category) for f in frontier]
    if None in scores[0]:
      # if one of the scores is None, give up. something is wrong with one of the puzzles.  
      # this will trigger a reroll
      return None

    # filter pareto
    scores = sorted(set(scores))
    scores = [a for a in scores if not any(pareto_compare(a, b) for b in scores)]
    return scores

  def get_discord_announcement(self):
    date = datetime.fromordinal(self.day)
    scores_names = score_string(self.start_scores, self.category)
    link = self.get_leaderboard_link()
    return discord.Embed(color=discord.Color.dark_gold(),title=f"Daily Pareto for {date:%B %d, %Y}", description=f"{self.flags}({self.category}) for {self.puzzle['displayName']}: [zlbb 🔗]({link})\n```{scores_names}```")

  def get_results_post(self,submitters):
    date = datetime.fromordinal(self.day)
    filename = self.make_chart()
    embed = discord.Embed(color=discord.Color.gold(),title=f"Daily Pareto Results for {date:%B %d, %Y}", description=f"Submissions by {', '.join(submitters)}")
    embed.set_image(url=f'attachment://{filename}')
    return embed

  def get_leaderboard_link(self):
    link = f'https://zlbb.faendir.com/puzzles/{self.puzzle["id"]}/visualizer?visualizerFilter-{self.puzzle["id"]}.showOnlyFrontier=true&visualizerConfig.mode=2D&visualizerConfig.x={self.category[0].lower()}&visualizerConfig.y={self.category[1].lower()}'
    if 'O' not in self.flags:
      link += f'&visualizerFilter-{self.puzzle["id"]}.modifiers.overlap=false'
    if 'T' in self.flags:
      link += f'&visualizerFilter-{self.puzzle["id"]}.modifiers.trackless=true'
    try:
      if self.min_flag in self.flags:
        link += f'&visualizerFilter-{self.puzzle["id"]}.range.{self.min_flag.lower()}.max={self.min_score}'
    except AttributeError:
      pass
    return link

  def make_chart(self, local_name='yesterday'):
    date = datetime.fromordinal(self.day)
    pl.clf()
    pl.figure(figsize=(8, 6), layout='tight')
    pl.rc('font', size=18)
    pl.style.use('dark_background')
    pl.title(f"{self.flags}({self.category}) for {self.puzzle['displayName']} {date:%Y-%m-%d} ")
    pl.xlabel(catmap[self.category[0]])
    pl.ylabel(catmap[self.category[1]])
    pl.grid(True, which='both', alpha=.25)
    if True:
      pl.loglog()
      ax = pl.gca()
  
      # I like more labels on the lines than it gives by default.  but this is a little bit too much sometimes
      ax.xaxis.set_major_formatter(mticker.ScalarFormatter())
      ax.xaxis.set_minor_formatter(mticker.ScalarFormatter())
      ax.yaxis.set_major_formatter(mticker.ScalarFormatter())
      ax.yaxis.set_minor_formatter(mticker.ScalarFormatter())
      ax.spines['top'].set_visible(False)
      ax.spines['right'].set_visible(False)
  
    # still looking for a good way to render infinities, but filtering them out for now
    start = set(tuple(s) for s in self.start_scores if np.inf not in s)
    end = set(tuple(s) for s in self.end_scores if np.inf not in s)
  
    dead = start.difference(end)
    keep = start.intersection(end)
    new = end.difference(start)
    if len(dead): pl.plot(*zip(*dead), 'ro', ms=8, fillstyle='none')
    if len(keep): pl.plot(*zip(*keep), 'ro', ms=8)
    if len(new): pl.plot(*zip(*new), 'go', ms=8)
  
    pl.savefig(f'{local_name}.png')
    with open(f'{local_name}.png','rb') as f:
      dbx.files_upload(f.read(),f"/charts/{self.day}.png",mode=dropbox.files.WriteMode.overwrite)
    return f'{local_name}.png'

@discord.app_commands.checks.has_role('reroller')
@tree.command(name = "daily_reroll", description = "Reroll the daily.", guild=discord.Object(id=DISCORD_GUILD))
async def reroll(interaction):
  A = await client.fetch_channel(DISCORD_THREAD_1)
  async for message in A.history(limit=200):
      if message.author == client.user:
          await message.delete()
          break
  today = datetime.now()
  ordinal = today.toordinal()
  pick = Pick(ordinal,reroll=True)
  await A.send(embed=pick.get_discord_announcement())

@discord.app_commands.checks.has_role('reroller')
@tree.command(name = "test", description = "temporary test command.", guild=discord.Object(id=DISCORD_GUILD))
async def test(interaction):
  today = datetime.now()
  ordinal = today.toordinal()
  thread = await client.fetch_channel(DISCORD_THREAD_1)
  misc = await client.fetch_channel(DISCORD_THREAD_2)
  om = await client.fetch_channel(DISCORD_CHANNEL)
  leaderboard_bot = await client.fetch_user(DISCORD_LEADERBOARD_BOT)
  time = datetime.fromordinal(ordinal-1)+timedelta(hours=12)
  print(time)
  print(today)
  pick = Pick(ordinal-1)
  submitters = set()
  conditions = lambda x: ((x.author == leaderboard_bot) and (len(x.embeds) > 0) and \
  ('New Submission' in x.embeds[0].title) and \
  (re.search(r'\*(.*)\*',x.embeds[0].title).group(1) == pick.puzzle['displayName']))
  async for message in misc.history(after=time,limit=10):
    if conditions(message):
      print(message.author,message.embeds[0].title)
      submitters.add(re.search(r'by (.*) was', message.embeds[0].description).group(1))
  async for message in om.history(after=time,limit=20):
    if conditions(message):
      print(message.author,message.embeds[0].title)
      submitters.add(re.search(r'by (.*) was', message.embeds[0].description).group(1))
  with open('yesterday.png','rb') as f:
    await thread.send(embed=pick.get_results_post(submitters), file=discord.File(f,filename='yesterday.png'))
  await thread.send(embed=pick.get_discord_announcement())

@client.event
async def on_ready():
  await tree.sync(guild=discord.Object(id=DISCORD_GUILD))
  thread = await client.fetch_channel(DISCORD_THREAD_1)
  misc = await client.fetch_channel(DISCORD_THREAD_2)
  om = await client.fetch_channel(DISCORD_CHANNEL)
  leaderboard_bot = await client.fetch_user(DISCORD_LEADERBOARD_BOT)
  await thread.join()
  conditions = lambda x: ((x.author == leaderboard_bot) and (len(x.embeds) > 0) and \
  ('New Submission' in x.embeds[0].title) and \
  (re.search(r'\*(.*)\*',x.embeds[0].title).group(1) == pick.puzzle['displayName']))
  print('Ready!')
  while True:
    today = datetime.now()
    ordinal = today.toordinal()
    try:
      last_upload = int([i.name for i in dbx.files_list_folder('/charts').entries][-1].split('.')[0])
    except IndexError:
      last_upload = 1
    announce_time = datetime.fromordinal(ordinal+(last_upload+1 == ordinal)) + timedelta(hours=12)
#    announce_time = today + timedelta(minutes=1)
    await discord.utils.sleep_until(announce_time)
    submitters = set()
    async for message in misc.history(after=announce_time,limit=10):
      if conditions(message):
        print(message.author,message.embeds[0].title)
        submitters.add(re.search(r'by (.*) was', message.embeds[0].description).group(1))
    async for message in om.history(after=announce_time,limit=20):
      if conditions(message):
        print(message.author,message.embeds[0].title)
        submitters.add(re.search(r'by (.*) was', message.embeds[0].description).group(1))
    today = datetime.now()
    ordinal = today.toordinal()
    pick_old = Pick(ordinal-1)
    pick = Pick(ordinal)
    pick_old.make_chart('yesterday')
    with open('yesterday.png','rb') as f:
      await thread.send(embed=pick_old.get_results_post(submitters), 
 file=discord.File(f,filename='yesterday.png'))
    await thread.send(embed=pick.get_discord_announcement())

reload = False
if reload or not os.path.isfile('puzzle.json'):
  puzzles = requests.get('https://zlbb.faendir.com/om/puzzles').json()
  with open('puzzles.json', 'w') as file:
    json.dump(puzzles, file)
else:
  with open('puzzles.json', 'r') as file:
    puzzles = json.load(file)

# TODO: filter out puzzles like stab water

chapter_puzzles = tuple(filter(lambda x: 'CHAPTER' in x['group']['id'], puzzles))
journal_puzzles = tuple(filter(lambda x: 'JOURNAL' in x['group']['id'], puzzles))
tournament_puzzles = tuple(filter(lambda x: 'TOURNAMENT' in x['group']['id'], puzzles))

grouped_puzzles = np.array([chapter_puzzles, journal_puzzles, tournament_puzzles], dtype=object)
grouped_puzzles_weight = normed([3, 2, 1])

# I'd kind of like to do this in a better datastructure so the names/weights are more closely tied together
categories = ['G', 'C', 'A', 'I', 'R', 'H', 'W']
categories_weight = [[2, 2, 2, 2, 1, 1, 1], [2, 2, 2, 2, 1, 0, 0]]  # disable AHW in production.  probably needs a cleaner way
catmap = {'G': 'cost','C': 'cycles','A': 'area','I': 'instructions','R': 'rate','H': 'height','W': 'width'}

client.run(DISCORD_BOT_TOKEN)




#  for i in ['today', 'yesterday']:
#    ordinal = datetime.date.today().toordinal() - (i=='yesterday')
#    pick = Pick(ordinal)
#    print(pick)
#    print(pick.get_discord_announcement())
#    pick.make_chart(i)
#    print()
  
# lines to generate access token and refresh token, need to make it more formal later.
#A = dropbox.oauth.DropboxOAuth2FlowNoRedirect(DROPBOX_APP_KEY,DROPBOX_APP_SECRET,token_access_type='offline')
#print(A.start())
#auth_code = input()
#B = A.finish(auth_code)
#print(f"Access Token: {B.access_token}\nRefresh Token: {B.refresh_token}")

