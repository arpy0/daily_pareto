import os, json, re
import dropbox
import discord
from time import sleep
from pprint import pformat
from discord import app_commands
from datetime import datetime,timedelta,timezone

import numpy as np
import matplotlib.ticker as mticker
import matplotlib.pyplot as pl
import requests
import logging


# Assume client refers to a discord.Client subclass...
# Various tokens and IDs stored as secrets on Replit servers
DISCORD_BOT_TOKEN = os.environ['DISCORD_BOT_TOKEN']
DISCORD_GUILD = os.environ['DISCORD_GUILD']
DISCORD_CHANNEL = os.environ['DISCORD_CHANNEL']
DISCORD_THREAD = os.environ['DISCORD_THREAD']
DISCORD_LEADERBOARD_BOT = os.environ['DISCORD_LEADERBOARD_BOT']
DROPBOX_APP_KEY = os.environ['DROPBOX_APP_KEY']
DROPBOX_APP_SECRET = os.environ['DROPBOX_APP_SECRET']
DROPBOX_ACCESS_TOKEN = os.environ['DROPBOX_ACCESS_TOKEN']
DROPBOX_REFRESH_TOKEN = os.environ['DROPBOX_REFRESH_TOKEN']

# Setup for the discord bot, declaring intented uses
intents = discord.Intents(messages=True, message_content=True, members=True, guilds=True)
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

dbx = dropbox.Dropbox(oauth2_access_token=DROPBOX_ACCESS_TOKEN,oauth2_refresh_token=DROPBOX_REFRESH_TOKEN,
                      app_key=DROPBOX_APP_KEY,app_secret=DROPBOX_APP_SECRET)

# TODO: Add logging functionality

def normed(x):
  """Convenience function to reduce a positive array to percentages."""
  return x / np.sum(x)

def pareto_compare(a, b) -> bool:
  """Convenience function to return True if b is ever better in a pareto sense than A"""
  # Probably can be simplified to a one-liner, but it's fine.
  if a == b:
    return False
  return all(z[0] >= z[1] for z in zip(a, b))

def score_string(scores, category) -> str:
  """Power one-liner to make a score string out of scores, looks like '100a/90b, 89a/80b', etc."""
  return ', '.join(['/'.join([''.join(z) for z in zip(map(str, s), category.lower())]) for s in scores])

class Pick():
  """
  The default class used to generate, store, and load the pick for the daily pareto.
  All attributes should be JSON-handleable.
  """
  def __init__(self, ordinal, reroll=False):
    """
    Load or generate a pick based on its ordinal, the number of days since 01-01-0001
    End scores will be from start scores after the initial generation assuming submissions have been made.
    """
    self.day = ordinal
    if reroll or not self.load():
      self.start_scores = self.random()
    self.end_scores = self.get_frontier()
    self.save()

  def __repr__(self):
    """Printing a pick object will provide the pprint formatted version of its attributes"""
    return pformat(self.__dict__)

  def load(self) -> bool:
    """Tries to load the pick from Dropbox, if it fails, return False."""
    try:
      data = dbx.files_download(f"/picks/{self.day}.txt")[1]
      data_json = json.loads(data.content)
      for i in data_json:
        self.__dict__[i] = data_json[i]
      return True
    except dropbox.exceptions.ApiError:
      return False

  def save(self):
    """Saves the pick to Dropbox, as a JSON"""
    # Note that datetime objects are not JSON serializable so they should not be included as a class object
    dbx.files_upload(bytes(json.dumps(self.__dict__,indent=4),'utf-8'),f"/picks/{self.day}.txt",
                     mode=dropbox.files.WriteMode.overwrite)

  def random(self,reload=False):
    """
    Generate a pick composed of puzzle and category based on weighted probabilities.
    Also retrieves currently available scores related to the pick.
    """
    # Loads the puzzles from zlbb, or reloads them if desired.
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
    
    # The stock puzzles are given more precedence than journal puzzles due to ease and familiarity
    grouped_puzzles = np.array([chapter_puzzles, journal_puzzles, tournament_puzzles], dtype=object)
    grouped_puzzles_weight = normed([3, 2, 1])
    
    # Height and Width are unavailable in production since all solutions will be identical (or cheated) there
    # FUTURE: Account for manifold split
    categories = ['G', 'C', 'A', 'I', 'H', 'W']
    categories_weight = [[3, 3, 2, 3, 1, 1], [1, 1, 1, 1, 0, 0]]
    catmap = [{'G':'cost', 'C':'cycles', 'A':'area', 'I':'instructions', 'H':'height', 'W':'width'},
              {'G':'cost', 'R':'rate', 'A':'areaINF', 'I':'instructions', 'H':'heightINF', 'W':'widthINF'}]
    
    catlink = [{'G':'g', 'C':'c', 'A':'a', 'I':'i', 'H':'h', 'W':'w'},
               {'G':'g', 'R':'r', 'A':'aI', 'I':'i', 'H':'hI', 'W':'wI'}]
    
    
    rng = np.random.default_rng()
    self.puzzle = rng.choice(rng.choice(grouped_puzzles, p=grouped_puzzles_weight))
    scores = None
    while not scores:
      # If a bad category is rolled, scores will return None, so this process repeats until a good category is rolled.
      # The "min_flag" should be unique from the category, so they are generated together.
      # Each of the flags O, T, and min_flag, have a small chance of being added to the category thereafter.
      probs = normed(categories_weight[self.puzzle['type'] == 'PRODUCTION'])
      metrics = ''.join(rng.choice(categories, 3, False, p=probs))
      self.category, self.min_flag = ''.join(sorted(metrics[:2],key=lambda x: categories.index(x))), metrics[2]
      self.flags = ''.join(
        (rng.choice(['', 'O'], p=[0.99,0.01]),
         rng.choice(['', 'T'], p=[0.9, 0.1]),
         rng.choice(['', self.min_flag], p=[0.85, 0.15])))
      self.manifold = rng.choice(['V', 'INF'], p=[0.7,0.3])
      mani_bool = self.manifold == 'INF'
      
      if mani_bool: self.category = self.category.replace('C','R')
      if mani_bool: self.min_flag = self.min_flag.replace('C','R')
      if mani_bool: self.flags = self.flags.replace('C','R')
      self.cat_long = [catmap[mani_bool][i] for i in self.category]
      self.min_long = catmap[mani_bool][self.min_flag]
      self.cat_link = [catlink[mani_bool][i] for i in self.category]
      self.min_link = catlink[mani_bool][self.min_flag]

      print(pformat(self.__dict__))
      scores = self.get_frontier()
      print(scores)
    return scores

  def get_frontier(self):
    """
    Gets the entire pareto frontier from zlbb, 
    and filters it down to just the category the pick is working with.
    """
    url = f'https://zlbb.faendir.com/om/puzzle/{self.puzzle["id"]}/records?includeFrontier=true'
    frontier = requests.get(url).json()
    frontier = tuple(filter(lambda x: x['score'][self.cat_long[0]] != None, frontier))
    frontier = tuple(filter(lambda x: x['score'][self.cat_long[1]] != None, frontier))
    if 'O' not in self.flags:
      frontier = tuple(filter(lambda x: x['score']['overlap'] == False, frontier))
    if 'T' in self.flags:
      frontier = tuple(filter(lambda x: x['score']['trackless'] == True, frontier))
    if self.min_flag in self.flags:
      self.min_score = min(np.inf if i['score'][self.min_long] in ['∞','Infinity'] 
                           else i['score'][self.min_long] for i in frontier)
      frontier = tuple(filter(lambda x: x['score'][self.min_long] == self.min_score,frontier))

    scores = [tuple(np.inf if f['score'][c] in ['∞','Infinity']
                    else f['score'][c] for c in self.cat_long) for f in frontier]
    if (scores is None):
      # If a score is ever None, something's gone wrong, return None and reroll.
      return None

    scores = sorted(set(scores))
    scores = [a for a in scores if not any(pareto_compare(a, b) for b in scores)]
    return scores

  def get_discord_announcement(self,auto=True):
    """Generates a Discord embed for the bot to post indicating the category for the day."""
    date = datetime.fromordinal(self.day)
    filename = self.make_chart('today')
    scores = score_string(self.start_scores, self.category)
    link = self.get_leaderboard_link()
    if auto:
      embed = discord.Embed(color=discord.Color.dark_gold(),
                            title=f"Daily Pareto for {date:%B %d, %Y}",
                            description=f"{self.flags}({self.category})@{self.manifold} for {self.puzzle['displayName']}: [zlbb 🔗]({link})\n```{scores}```",
                            timestamp=date + timedelta(hours=12))
      embed.set_image(url=f'attachment://{filename}')
      return embed
    else:
      return f"```Daily Pareto for {date:%B %d, %Y}:\n{self.flags}({self.category})@{self.manifold} for {self.puzzle['displayName']}\n{scores}```\n<{link}>\n"

  
  def get_results_post(self,submitters,auto=True):
    """Generates a Discord embed for the bot to post indicating the results for yesterday."""
    date = datetime.fromordinal(self.day)
    filename = self.make_chart('yesterday')
#    description = f"Submissions by {', '.join(submitters)}" if submitters else "No submissions"
    description = None
    if auto:
      embed = discord.Embed(color=discord.Color.gold(),
                            title=f"Daily Pareto Results for {date:%B %d, %Y}", 
                            description=description)
      embed.set_image(url=f'attachment://{filename}')
      return embed
  
  def get_leaderboard_link(self):
    """Generates a URL link to zlbb based on puzzle, category, and set flags."""
    link = f'https://zlbb.faendir.com/puzzles/{self.puzzle["id"]}/visualizer?visualizerFilter-{self.puzzle["id"]}.showOnlyFrontier=true&visualizerConfig.mode=2D&visualizerConfig.x={self.cat_link[0]}&visualizerConfig.y={self.cat_link[1]}'
    if 'O' not in self.flags:
      link += f'&visualizerFilter-{self.puzzle["id"]}.modifiers.overlap=false'
    if 'T' in self.flags:
      link += f'&visualizerFilter-{self.puzzle["id"]}.modifiers.trackless=true'
    if self.min_flag in self.flags:
      link += f'&visualizerFilter-{self.puzzle["id"]}.range.{self.min_link}.max={self.min_score}'
    return link

  def make_chart(self, local_name='yesterday'):
    """
    Generates a plot showing the results of the daily, comparing current scores to starting scores.
    Returns the local file name of the chart (with file extension).
    """
    date = datetime.fromordinal(self.day)
    pl.clf()
    pl.figure(figsize=(8, 6), layout='tight')
    pl.rc('font', size=18)
    pl.style.use('dark_background')
    pl.title(f"{self.flags}({self.category})@{self.manifold} for {self.puzzle['displayName']} {date:%Y-%m-%d} ")
    pl.xlabel(self.category[0])
    pl.ylabel(self.category[1])
    pl.grid(True, which='both', alpha=.25)
    pl.loglog()
    ax = pl.gca()
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Adds additional gridlines, though this might sometimes cause overlap in tick labels
    ax.xaxis.set_major_formatter(mticker.ScalarFormatter())
    ax.xaxis.set_minor_formatter(mticker.ScalarFormatter())
    ax.yaxis.set_major_formatter(mticker.ScalarFormatter())
    ax.yaxis.set_minor_formatter(mticker.ScalarFormatter())
  
    # Infinities are not handled presently, perhaps in the future.
    start = set(tuple(s) for s in self.start_scores if np.inf not in s)
    end = set(tuple(s) for s in self.end_scores if np.inf not in s)

    # TODO: if minflag is set, handle case where min is improved
    dead = start.difference(end)
    keep = start.intersection(end)
    new = end.difference(start)
    if len(dead): pl.plot(*zip(*dead), 'ro', ms=8, fillstyle='none')
    if len(keep): pl.plot(*zip(*keep), 'ro', ms=8)
    if len(new): pl.plot(*zip(*new), 'go', ms=8)
  
    pl.savefig(f'{local_name}.png')
    if local_name == 'yesterday':
      with open(f'{local_name}.png','rb') as f:
        dbx.files_upload(f.read(),f"/charts/{self.day}.png",mode=dropbox.files.WriteMode.overwrite)
    return f'{local_name}.png'

@discord.app_commands.checks.has_role('reroller')
@tree.command(name = "daily_reroll", description = "Reroll the daily.", guild=discord.Object(id=DISCORD_GUILD))
async def reroll(interaction):
  """Discord command to reroll the daily, also deleting the previous mention of it."""
  A = await client.fetch_channel(DISCORD_THREAD)
  async for message in A.history(limit=200):
      if message.author == client.user:
          await message.delete()
          break
  today = datetime.now(timezone.utc)
  ordinal = today.toordinal()
  pick = Pick(ordinal,reroll=True)
  pick.make_chart('today')
  with open('today.png','rb') as f:
    await A.send(embed=pick.get_discord_announcement(),
                 file=discord.File(f,filename='today.png'))
  
@client.event
async def on_ready():
  """Discord ready event, starts/resumes the loop to post the daily."""
  await tree.sync(guild=discord.Object(id=DISCORD_GUILD))
  thread = await client.fetch_channel(DISCORD_THREAD)
#  om = await client.fetch_channel(DISCORD_CHANNEL)
#  leaderboard_bot = await client.fetch_user(DISCORD_LEADERBOARD_BOT)
  await thread.join()
  print('Ready!')

  # Series of conditions to determine if a message is by the leaderboard bot and is a submission message
  # Pretty fragile as a result, but works for now.
#  conditions = lambda x: ((x.author == leaderboard_bot) and (len(x.embeds) > 0) and \
#  (x.embeds[0].title != None) and ('New Submission' in x.embeds[0].title) and \
#  (re.search(r'\*(.*)\*',x.embeds[0].title).group(1) == pick.puzzle['displayName']))
  while True:
    today = datetime.now(timezone.utc)
    ordinal = today.toordinal()
    last_upload = int([i.name for i in dbx.files_list_folder('/charts').entries][-1].split('.')[0])
    # The daily is announced at noon UTC
    announce_time = datetime.fromordinal(ordinal+(last_upload+1 == ordinal)) + timedelta(hours=12)
    print(announce_time)
    await discord.utils.sleep_until(announce_time)
    # The day has to be recalculated in case of bot reconnections and such
    today = datetime.now(timezone.utc)
    ordinal = today.toordinal()
    pick_old = Pick(ordinal-1)
    pick = Pick(ordinal)
    submitters = set()
    # Checks the two channels for any submssions (is this an API expensive operation?)
    # Would be ideal to just check messages from the leaderboard bot but this doesn't seem to work
#    async for message in thread.history(before=announce_time,limit=1000):
#      if conditions(message):
#        print(message.author,message.embeds[0].title)
#        submitters.add(re.search(r'by (.*) was', message.embeds[0].description).group(1))
  
#    async for message in om.history(before=announce_time,limit=200):
#      if conditions(message):
#        print(message.author,message.embeds[0].title)
#        submitters.add(re.search(r'by (.*) was', message.embeds[0].description).group(1))
    print(submitters)
    pick_old.make_chart('yesterday')
    with open('yesterday.png','rb') as f:
      await thread.send(embed=pick_old.get_results_post(submitters), 
                        file=discord.File(f,filename='yesterday.png'))
    pick.make_chart('today')
    with open('today.png','rb') as f:
      await thread.send(embed=pick.get_discord_announcement(),
                        file=discord.File(f,filename='today.png'))
    sleep(60)

# This line must be run after all the Discord commands are defined.
auto = True
if auto:
  sleep(5)
  handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
  client.run(DISCORD_BOT_TOKEN, log_handler = handler)

else:
  ordinal = datetime.today().toordinal()
  pick_old = Pick(ordinal-1)
  pick = Pick(ordinal,reroll=bool(input('Reroll? ')))
  print(pick)
  print(pick.get_discord_announcement(auto=False))
  pick_old.make_chart()
  print()
    
# lines to generate access token and refresh token, need to make it more formal later.
#A = dropbox.oauth.DropboxOAuth2FlowNoRedirect(DROPBOX_APP_KEY,DROPBOX_APP_SECRET,token_access_type='offline')
#print(A.start())
#auth_code = input()
#B = A.finish(auth_code)
#print(f"Access Token: {B.access_token}\nRefresh Token: {B.refresh_token}")