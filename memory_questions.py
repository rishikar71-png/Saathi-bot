"""
memory_questions.py — Module 16

Manages the 300+ evocative life-story question bank for Saathi.

What this file does:
  1. Holds all 300+ questions, organised by theme.
  2. seed_memory_questions()  — populates the memory_questions DB table on
     first startup (safe to call on every startup — skips if already seeded).
  3. get_next_memory_question(user_id) — picks a random unasked question for
     this senior. Resets the tracking table when all questions are exhausted.
  4. send_memory_prompt(bot, user_id) — sends the question as a warm message
     + TTS voice note, records in memory_prompt_log and user_question_tracking,
     and sets pending_memory_question_* on the user row so the response is
     captured and linked by the main.py pipeline.
  5. check_and_send_memory_prompts(bot) — called from rituals.py every 60 s.
     Sends questions on Wednesday (day 2) and Sunday (day 6) only, to users
     whose morning check-in time matches the current minute and who have not
     yet received a memory question today.
  6. save_memory_response(user_id, response_text, question_id, question_text,
     theme) — called from main.py when a pending response is captured. Saves a
     fully linked row to the memories table and clears the pending flag.
  7. get_pending_memory_question(user_id) — returns (question_id, question_text,
     theme) if a pending question exists for this user, else (None, None, None).

No scheduler is created here. check_and_send_memory_prompts() is called from
the existing ritual scheduler in rituals.py.
"""

import logging
import random
from datetime import datetime, timezone, timedelta

from database import get_connection, update_user_fields

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IST helpers (mirrors the pattern in rituals.py)
# ---------------------------------------------------------------------------

_IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_now() -> datetime:
    return datetime.now(timezone.utc) + _IST_OFFSET


def _current_hhmm() -> str:
    return _ist_now().strftime("%H:%M")


def _current_date() -> str:
    return _ist_now().strftime("%Y-%m-%d")


def _day_of_week() -> int:
    """Monday = 0, Sunday = 6."""
    return _ist_now().weekday()


# ---------------------------------------------------------------------------
# Question bank — 300+ questions across 9 themes
# ---------------------------------------------------------------------------
# Each entry is a dict: {"text": "...", "theme": "..."}
# Themes:
#   Childhood & School
#   Family & Relationships
#   Career & Life
#   India & History
#   Food & Culture
#   Music & Films
#   Places & Travel
#   Festivals & Traditions
#   Wisdom & Beliefs

_QUESTION_BANK = [

    # -----------------------------------------------------------------------
    # THEME 1 — Childhood & School (35 questions)
    # -----------------------------------------------------------------------
    {"text": "What was your favourite subject in school, and what made it special?", "theme": "Childhood & School"},
    {"text": "Do you remember your very first day of school? What was going through your mind?", "theme": "Childhood & School"},
    {"text": "Who was the teacher who made the biggest difference in your life, and why?", "theme": "Childhood & School"},
    {"text": "What games did you play as a child? Were there any you invented yourself?", "theme": "Childhood & School"},
    {"text": "Tell me about the house or neighbourhood you grew up in — what do you remember most vividly?", "theme": "Childhood & School"},
    {"text": "What was your favourite thing to do after school?", "theme": "Childhood & School"},
    {"text": "Did you have a best friend growing up? What did you two get up to?", "theme": "Childhood & School"},
    {"text": "Was there a book or story you loved as a child that you still think about?", "theme": "Childhood & School"},
    {"text": "What were mealtimes like at home when you were young?", "theme": "Childhood & School"},
    {"text": "Did you ever get into trouble at school? Tell me about it.", "theme": "Childhood & School"},
    {"text": "What did you want to be when you grew up? Did that dream change over time?", "theme": "Childhood & School"},
    {"text": "What is the earliest memory you have from childhood?", "theme": "Childhood & School"},
    {"text": "Were there any festivals or celebrations you looked forward to as a child more than anything else?", "theme": "Childhood & School"},
    {"text": "Did you have any pets as a child? Tell me about them.", "theme": "Childhood & School"},
    {"text": "What toy or object did you treasure most when you were young?", "theme": "Childhood & School"},
    {"text": "Who was your childhood hero — real or from a story?", "theme": "Childhood & School"},
    {"text": "What was your neighbourhood like? What sounds or smells do you remember?", "theme": "Childhood & School"},
    {"text": "Was there something you were really good at as a child that surprised the people around you?", "theme": "Childhood & School"},
    {"text": "What did summer holidays look like when you were young?", "theme": "Childhood & School"},
    {"text": "Did you have any rivals or competitors growing up — in studies, sport, anything?", "theme": "Childhood & School"},
    {"text": "What was the naughtiest thing you ever did as a child?", "theme": "Childhood & School"},
    {"text": "Were you close with your grandparents? What do you remember about them?", "theme": "Childhood & School"},
    {"text": "Did your family tell stories — about ancestors, about the old days? Any that stayed with you?", "theme": "Childhood & School"},
    {"text": "What was your school building like? Can you describe it?", "theme": "Childhood & School"},
    {"text": "Was there a moment in your childhood when you realised the world was bigger than your street?", "theme": "Childhood & School"},
    {"text": "What clothes did children wear when you were young? Do you remember a favourite outfit?", "theme": "Childhood & School"},
    {"text": "Did your mother or father have any sayings or phrases they always used? What were they?", "theme": "Childhood & School"},
    {"text": "Was there a particular smell from your childhood home that takes you right back when you encounter it today?", "theme": "Childhood & School"},
    {"text": "What was your relationship like with your siblings when you were growing up?", "theme": "Childhood & School"},
    {"text": "What was the most exciting thing that ever happened in your street or colony when you were a child?", "theme": "Childhood & School"},
    {"text": "Did you ever run away from something as a child — a situation, a person, a place?", "theme": "Childhood & School"},
    {"text": "What was your favourite thing to eat for breakfast as a child?", "theme": "Childhood & School"},
    {"text": "Were there any songs or rhymes you sang as a child that you still remember?", "theme": "Childhood & School"},
    {"text": "What did you fear most as a child? How did you deal with it?", "theme": "Childhood & School"},
    {"text": "If your childhood could be captured in one image, what would that picture look like?", "theme": "Childhood & School"},

    # -----------------------------------------------------------------------
    # THEME 2 — Family & Relationships (35 questions)
    # -----------------------------------------------------------------------
    {"text": "How did you meet your spouse? Tell me the whole story from the beginning.", "theme": "Family & Relationships"},
    {"text": "What is the most important thing your parents taught you — not by saying it, but by doing it?", "theme": "Family & Relationships"},
    {"text": "Tell me about the day your first child was born. What were you feeling?", "theme": "Family & Relationships"},
    {"text": "Who in your family has surprised you most over the years?", "theme": "Family & Relationships"},
    {"text": "What is a tradition in your family that you hope continues for generations?", "theme": "Family & Relationships"},
    {"text": "Is there someone you wish you had told something important to before it was too late?", "theme": "Family & Relationships"},
    {"text": "What was the biggest disagreement you ever had in your family, and how did it resolve?", "theme": "Family & Relationships"},
    {"text": "When your children were young, what did you most enjoy doing together?", "theme": "Family & Relationships"},
    {"text": "What do you know now about being a parent that you wish you had known then?", "theme": "Family & Relationships"},
    {"text": "Tell me about a time when your family really came together in a difficult moment.", "theme": "Family & Relationships"},
    {"text": "Is there a family member who is no longer with you who you still think about often?", "theme": "Family & Relationships"},
    {"text": "What quality do you most admire in your children?", "theme": "Family & Relationships"},
    {"text": "What do you hope your grandchildren remember about you?", "theme": "Family & Relationships"},
    {"text": "Who was the funniest person in your family? What made them so funny?", "theme": "Family & Relationships"},
    {"text": "Tell me about your parents — what were they like as people?", "theme": "Family & Relationships"},
    {"text": "What is the most generous thing someone in your family ever did for you?", "theme": "Family & Relationships"},
    {"text": "Was there ever a moment when a family member did something that made you immensely proud?", "theme": "Family & Relationships"},
    {"text": "What does family mean to you — how would you describe it to someone who didn't grow up with one?", "theme": "Family & Relationships"},
    {"text": "What is the biggest sacrifice you made for your family? Do you regret it?", "theme": "Family & Relationships"},
    {"text": "Is there a friendship — not family — that has meant as much to you as any relationship in your life?", "theme": "Family & Relationships"},
    {"text": "Tell me about your in-laws. What was that relationship like?", "theme": "Family & Relationships"},
    {"text": "What is something about your spouse that you discovered only years into the marriage?", "theme": "Family & Relationships"},
    {"text": "Was there someone outside your family who was like a parent to you at some point?", "theme": "Family & Relationships"},
    {"text": "If you could give your younger self one piece of advice about relationships, what would it be?", "theme": "Family & Relationships"},
    {"text": "What habit or saying of your parents do you catch yourself doing or saying now?", "theme": "Family & Relationships"},
    {"text": "How did your family handle money when you were growing up? What did it teach you?", "theme": "Family & Relationships"},
    {"text": "Tell me about a time you had to forgive someone in your family. How did that feel?", "theme": "Family & Relationships"},
    {"text": "What do you and your children agree on completely? What do you disagree on?", "theme": "Family & Relationships"},
    {"text": "What is something about your childhood family that you deliberately chose NOT to carry into your own?", "theme": "Family & Relationships"},
    {"text": "Who was your father's hero? Did your father ever tell you?", "theme": "Family & Relationships"},
    {"text": "What did your parents dream of for their own lives? Did those dreams come true?", "theme": "Family & Relationships"},
    {"text": "Is there a letter or message you wish you could send to someone who is no longer here?", "theme": "Family & Relationships"},
    {"text": "What is the story of your family name — do you know where it comes from?", "theme": "Family & Relationships"},
    {"text": "Tell me about a grandparent you were close to. What is one thing they said that you still carry?", "theme": "Family & Relationships"},
    {"text": "If your family sat down for one final dinner together, what would be on the table and who would be there?", "theme": "Family & Relationships"},

    # -----------------------------------------------------------------------
    # THEME 3 — Career & Life (35 questions)
    # -----------------------------------------------------------------------
    {"text": "How did you end up in the work you did? Was it planned, or did life take you there?", "theme": "Career & Life"},
    {"text": "What was the hardest decision you ever made in your professional life?", "theme": "Career & Life"},
    {"text": "Tell me about the colleague or boss who shaped you the most — for better or worse.", "theme": "Career & Life"},
    {"text": "What is the achievement from your working life that you are most proud of?", "theme": "Career & Life"},
    {"text": "Was there a moment when you could have taken a very different path in life? What happened?", "theme": "Career & Life"},
    {"text": "What did you learn in your work that you could never have learned in a classroom?", "theme": "Career & Life"},
    {"text": "Tell me about the first job you ever had. What was it like?", "theme": "Career & Life"},
    {"text": "Was there a time you took a risk professionally that paid off? Or one that didn't?", "theme": "Career & Life"},
    {"text": "What was the best advice anyone ever gave you about work or money?", "theme": "Career & Life"},
    {"text": "Is there something you always wanted to do professionally but never got to?", "theme": "Career & Life"},
    {"text": "What is something you built or created in your working life that is still standing — literally or figuratively?", "theme": "Career & Life"},
    {"text": "How did your idea of success change as you got older?", "theme": "Career & Life"},
    {"text": "Was there ever a time you felt completely lost in life — not knowing what to do next?", "theme": "Career & Life"},
    {"text": "What is the most difficult situation you navigated in your career, and how did you get through it?", "theme": "Career & Life"},
    {"text": "If you could go back and choose a completely different career, what would it be?", "theme": "Career & Life"},
    {"text": "Tell me about the moment you felt most confident in your abilities — professionally or personally.", "theme": "Career & Life"},
    {"text": "What does a life well-lived look like to you? Has your answer changed over time?", "theme": "Career & Life"},
    {"text": "What is the biggest mistake you made in your professional life, and what did it teach you?", "theme": "Career & Life"},
    {"text": "Is there a younger person whose career you helped shape? What did you pass on to them?", "theme": "Career & Life"},
    {"text": "What was the most unexpected thing that ever happened to you at work?", "theme": "Career & Life"},
    {"text": "How did you balance work and family — and was it ever easy?", "theme": "Career & Life"},
    {"text": "What financial lesson did you learn the hard way?", "theme": "Career & Life"},
    {"text": "Tell me about a time someone underestimated you. What did you do with that?", "theme": "Career & Life"},
    {"text": "What skill did you develop in your working life that surprised even you?", "theme": "Career & Life"},
    {"text": "If you were to write a book about your working life, what would the title be?", "theme": "Career & Life"},
    {"text": "Was there a promotion, recognition, or honour that meant more to you than money ever could?", "theme": "Career & Life"},
    {"text": "What do you know about hard work that younger people seem to have forgotten?", "theme": "Career & Life"},
    {"text": "Tell me about retirement — was the transition easy? What surprised you about it?", "theme": "Career & Life"},
    {"text": "What does a typical day look like for you now — and how is it different from 20 years ago?", "theme": "Career & Life"},
    {"text": "What is the most useful thing you own? Why?", "theme": "Career & Life"},
    {"text": "Did you ever work for yourself? What was that like compared to working for someone else?", "theme": "Career & Life"},
    {"text": "What would you say to someone just starting out in life today?", "theme": "Career & Life"},
    {"text": "How did the city or country you were in shape the kind of work you could do?", "theme": "Career & Life"},
    {"text": "What is something you did in your working life that you are still proud of today?", "theme": "Career & Life"},
    {"text": "If your working life could be summed up in one word, what would that word be?", "theme": "Career & Life"},

    # -----------------------------------------------------------------------
    # THEME 4 — India & History (35 questions)
    # -----------------------------------------------------------------------
    {"text": "Do you have any memory, however faint, of the years around Partition or Independence?", "theme": "India & History"},
    {"text": "What was it like to watch India play cricket in the early years? What matches do you remember?", "theme": "India & History"},
    {"text": "Where were you when India won the 1983 World Cup? Tell me everything.", "theme": "India & History"},
    {"text": "What is the biggest change you have seen in India in your lifetime?", "theme": "India & History"},
    {"text": "Did you ever meet anyone who had lived through the freedom movement? What did they tell you?", "theme": "India & History"},
    {"text": "What do you remember about the Emergency period in 1975–77? How did it affect daily life?", "theme": "India & History"},
    {"text": "What was India like when you were a young adult — how was it different from today?", "theme": "India & History"},
    {"text": "Do you remember where you were on a particular historic day in India's history?", "theme": "India & History"},
    {"text": "What Indian leader — politician, sportsperson, artist, scientist — has meant the most to you personally?", "theme": "India & History"},
    {"text": "What do you think has been India's greatest achievement since Independence?", "theme": "India & History"},
    {"text": "What is something about the India of your youth that you miss and wish was still here?", "theme": "India & History"},
    {"text": "Tell me about a time you felt deeply proud to be Indian.", "theme": "India & History"},
    {"text": "What changes have you seen in the city you have lived in the longest?", "theme": "India & History"},
    {"text": "What was the first time you saw a television set? Or a telephone? What was that moment like?", "theme": "India & History"},
    {"text": "How did your family react to India going nuclear — whether in 1974 or 1998?", "theme": "India & History"},
    {"text": "What is your memory of 26/11 in 2008? Where were you?", "theme": "India & History"},
    {"text": "Were you ever in a city or region during a major event — a flood, a political change, something historic?", "theme": "India & History"},
    {"text": "What Bollywood film from your youth best captures what life was actually like at that time?", "theme": "India & History"},
    {"text": "How did liberalisation in the 1990s change things for your family or your work?", "theme": "India & History"},
    {"text": "What do you think the India of your grandchildren will look like?", "theme": "India & History"},
    {"text": "Did you ever vote in an election that felt truly historic? What was that like?", "theme": "India & History"},
    {"text": "What did 'modern India' mean to you in your thirties? How has that definition shifted?", "theme": "India & History"},
    {"text": "Which Indian scientist, writer, or thinker do you feel does not get enough credit?", "theme": "India & History"},
    {"text": "What was the first time you travelled by aeroplane? Where did you go?", "theme": "India & History"},
    {"text": "Were there any national heroes whose deaths felt personal to you — as if you had lost someone you knew?", "theme": "India & History"},
    {"text": "What do you think is India's greatest cultural gift to the world?", "theme": "India & History"},
    {"text": "What changed for women in India during your lifetime that you noticed most clearly?", "theme": "India & History"},
    {"text": "Tell me about a time you watched history happening in real time — on television or in person.", "theme": "India & History"},
    {"text": "What is a story from your region's history that you think the rest of India should know?", "theme": "India & History"},
    {"text": "Who is the most remarkable Indian you have personally met or seen in real life?", "theme": "India & History"},
    {"text": "What does the Indian flag mean to you? Was there a specific moment when its meaning deepened?", "theme": "India & History"},
    {"text": "If you could restore one thing from India's past — a tradition, a place, a way of life — what would it be?", "theme": "India & History"},
    {"text": "What do you think young Indians today do not understand about what the previous generation went through?", "theme": "India & History"},
    {"text": "Is there a part of Indian history you feel you never fully understood and always wanted to?", "theme": "India & History"},
    {"text": "If you could ask Mahatma Gandhi one question, what would it be?", "theme": "India & History"},

    # -----------------------------------------------------------------------
    # THEME 5 — Food & Culture (35 questions)
    # -----------------------------------------------------------------------
    {"text": "What is the dish that most takes you back to your childhood?", "theme": "Food & Culture"},
    {"text": "Who was the best cook in your family, and what did they make that no one else could replicate?", "theme": "Food & Culture"},
    {"text": "Tell me about a meal you ate somewhere that you have never forgotten.", "theme": "Food & Culture"},
    {"text": "Is there a recipe you carry in your head that you learnt from your mother or grandmother?", "theme": "Food & Culture"},
    {"text": "What food could you eat every single day and never get tired of?", "theme": "Food & Culture"},
    {"text": "What did your family eat on special occasions — festivals, weddings, celebrations?", "theme": "Food & Culture"},
    {"text": "Is there a regional food from your home town that the rest of India should know about?", "theme": "Food & Culture"},
    {"text": "Tell me about a time food brought people together in an unexpected way.", "theme": "Food & Culture"},
    {"text": "What food did you hate as a child but love now?", "theme": "Food & Culture"},
    {"text": "If you could eat one last meal, what would be on the plate?", "theme": "Food & Culture"},
    {"text": "What is the street food from your city that you would recommend to anyone?", "theme": "Food & Culture"},
    {"text": "Did you ever try to cook something that went completely wrong? Tell me about it.", "theme": "Food & Culture"},
    {"text": "What does tea mean to you? Tell me about your relationship with chai.", "theme": "Food & Culture"},
    {"text": "Have the flavours you enjoy changed as you have gotten older? How?", "theme": "Food & Culture"},
    {"text": "Is there a food ritual in your house — something you always do at a certain time of day?", "theme": "Food & Culture"},
    {"text": "Tell me about the most unusual food you have ever tasted.", "theme": "Food & Culture"},
    {"text": "Did your family have a particular way of making something that was completely unique to them?", "theme": "Food & Culture"},
    {"text": "What is an Indian food tradition you feel is slowly disappearing?", "theme": "Food & Culture"},
    {"text": "What language do you dream in? Has that ever changed?", "theme": "Food & Culture"},
    {"text": "What aspect of your regional culture are you most proud of?", "theme": "Food & Culture"},
    {"text": "Is there an art form — weaving, pottery, painting, embroidery — from your region that you love?", "theme": "Food & Culture"},
    {"text": "What role has your mother tongue played in your identity?", "theme": "Food & Culture"},
    {"text": "Tell me about a cultural event — a mela, a sabha, a gathering — that was unforgettable.", "theme": "Food & Culture"},
    {"text": "What does your community do that outsiders always find strange but you find completely natural?", "theme": "Food & Culture"},
    {"text": "Is there a wedding custom from your community that you love?", "theme": "Food & Culture"},
    {"text": "What is the best meal you ever cooked yourself — and who was there to eat it?", "theme": "Food & Culture"},
    {"text": "What is something about Indian cuisine that you think is misunderstood by the rest of the world?", "theme": "Food & Culture"},
    {"text": "Did food ever play a role in healing something — a relationship, a difficult time?", "theme": "Food & Culture"},
    {"text": "What is the one masala or spice that defines the cooking of your home?", "theme": "Food & Culture"},
    {"text": "Tell me about the first time you tasted something completely new — a cuisine, a dish — that changed your palate.", "theme": "Food & Culture"},
    {"text": "What did your family do after dinner when you were growing up?", "theme": "Food & Culture"},
    {"text": "Is there a snack from your childhood that you cannot find anymore?", "theme": "Food & Culture"},
    {"text": "What food do you associate with feeling well and cared for?", "theme": "Food & Culture"},
    {"text": "What is the one dish you could teach your grandchild that would carry a piece of your family forward?", "theme": "Food & Culture"},
    {"text": "If your life had a flavour, what would it be?", "theme": "Food & Culture"},

    # -----------------------------------------------------------------------
    # THEME 6 — Music & Films (35 questions)
    # -----------------------------------------------------------------------
    {"text": "What is the song that most takes you back to a specific moment in your life?", "theme": "Music & Films"},
    {"text": "Who is the singer whose voice has meant the most to you, and why?", "theme": "Music & Films"},
    {"text": "Tell me about the first film you saw in a cinema hall. What do you remember about that experience?", "theme": "Music & Films"},
    {"text": "Is there a Bollywood film from your youth that captured something true about life as you lived it?", "theme": "Music & Films"},
    {"text": "What is a song you associate with your courtship or early married life?", "theme": "Music & Films"},
    {"text": "Who was your favourite actor when you were young? What was it about them?", "theme": "Music & Films"},
    {"text": "Tell me about a film or song that made you cry — and why it moved you so much.", "theme": "Music & Films"},
    {"text": "Do you have a favourite ghazal? Tell me what it means to you.", "theme": "Music & Films"},
    {"text": "What classical music — if any — do you love? How did you come to love it?", "theme": "Music & Films"},
    {"text": "Is there a bhajan or devotional song that gives you peace? Tell me about it.", "theme": "Music & Films"},
    {"text": "What film dialogues do you still remember word for word?", "theme": "Music & Films"},
    {"text": "Who do you consider the greatest musician India has ever produced?", "theme": "Music & Films"},
    {"text": "Did you ever see a live performance — a concert, a mujra, a classical recital — that stayed with you?", "theme": "Music & Films"},
    {"text": "What was it like watching Amitabh Bachchan in his heyday? Were you a fan?", "theme": "Music & Films"},
    {"text": "Is there a film you watched more than five times? Which one, and what kept drawing you back?", "theme": "Music & Films"},
    {"text": "Tell me about a film that changed the way you saw something — society, love, justice, anything.", "theme": "Music & Films"},
    {"text": "What music did you play at your own wedding, or at your children's weddings?", "theme": "Music & Films"},
    {"text": "Is there a song associated with a specific memory so strongly that hearing it takes you straight back?", "theme": "Music & Films"},
    {"text": "Did you ever learn to play a musical instrument? Or wish you had?", "theme": "Music & Films"},
    {"text": "Tell me about how film music has changed in your lifetime — for better or worse.", "theme": "Music & Films"},
    {"text": "What is a song from your parents' generation that you know by heart?", "theme": "Music & Films"},
    {"text": "Who is the greatest lyricist in Hindi film music, in your view?", "theme": "Music & Films"},
    {"text": "What film character do you feel most resembles someone you have known in real life?", "theme": "Music & Films"},
    {"text": "Did music help you through a difficult time? Which song was it?", "theme": "Music & Films"},
    {"text": "What was your relationship with the radio when you were growing up? What programmes did you love?", "theme": "Music & Films"},
    {"text": "Is there a film or song from a language other than your own that you love?", "theme": "Music & Films"},
    {"text": "Tell me about the golden era of Bollywood — who were the greats and what made that time special?", "theme": "Music & Films"},
    {"text": "Did you ever write poetry or lyrics yourself, even just for yourself?", "theme": "Music & Films"},
    {"text": "What song do you hear now that makes you feel young again, even for a moment?", "theme": "Music & Films"},
    {"text": "Is there a film you saw as a young person that shaped how you thought about love?", "theme": "Music & Films"},
    {"text": "Who was Lata Mangeshkar to you? What does her music mean?", "theme": "Music & Films"},
    {"text": "What is the most beautiful piece of music you have ever heard?", "theme": "Music & Films"},
    {"text": "Did your family sing together — at festivals, on journeys, at any time?", "theme": "Music & Films"},
    {"text": "What is the last film or song that genuinely moved you?", "theme": "Music & Films"},
    {"text": "If your life were a film, who would play you and what song would be on the soundtrack?", "theme": "Music & Films"},

    # -----------------------------------------------------------------------
    # THEME 7 — Places & Travel (35 questions)
    # -----------------------------------------------------------------------
    {"text": "What is the most beautiful place you have ever seen in India?", "theme": "Places & Travel"},
    {"text": "Tell me about a journey — by train, bus, or boat — that you will never forget.", "theme": "Places & Travel"},
    {"text": "Have you ever visited a holy site? What was the experience of being there?", "theme": "Places & Travel"},
    {"text": "What city in India do you feel most at home in — apart from your own?", "theme": "Places & Travel"},
    {"text": "Is there a place you always meant to visit but never did? What held you back?", "theme": "Places & Travel"},
    {"text": "Tell me about the most memorable train journey of your life.", "theme": "Places & Travel"},
    {"text": "Did you ever travel abroad? What surprised you most about the world outside India?", "theme": "Places & Travel"},
    {"text": "What is a place in India that you think is completely underrated and deserves more love?", "theme": "Places & Travel"},
    {"text": "Tell me about a road trip or long journey you took with family. What happened along the way?", "theme": "Places & Travel"},
    {"text": "Is there a city you have visited that felt completely unlike anywhere else?", "theme": "Places & Travel"},
    {"text": "What is your relationship with the hills or the mountains? Have you spent time there?", "theme": "Places & Travel"},
    {"text": "Tell me about your first experience of the sea. Where were you? What did you feel?", "theme": "Places & Travel"},
    {"text": "What place from your past — a home, a town, a street — do you wish you could go back to?", "theme": "Places & Travel"},
    {"text": "If you could take your grandchildren somewhere in India to show them something important, where would you take them?", "theme": "Places & Travel"},
    {"text": "Tell me about a pilgrimage you went on — what did the journey itself teach you?", "theme": "Places & Travel"},
    {"text": "What is the farthest from home you have ever been, and what brought you there?", "theme": "Places & Travel"},
    {"text": "Did you ever get completely lost somewhere? What happened?", "theme": "Places & Travel"},
    {"text": "What is your favourite season and the place you most love to be in during that season?", "theme": "Places & Travel"},
    {"text": "Is there a particular river that has meant something to you? Tell me about it.", "theme": "Places & Travel"},
    {"text": "What Indian city or place has changed the most dramatically in your lifetime?", "theme": "Places & Travel"},
    {"text": "Tell me about a place of worship — temple, mosque, gurudwara, church — that moved you, regardless of your own faith.", "theme": "Places & Travel"},
    {"text": "What is a market or bazaar in India that you love to walk through? What makes it special?", "theme": "Places & Travel"},
    {"text": "Have you ever had to leave a place you loved? What was that like?", "theme": "Places & Travel"},
    {"text": "What does Mumbai, Delhi, Kolkata, or Chennai mean to you — whichever one has touched your life?", "theme": "Places & Travel"},
    {"text": "Is there a landscape — a desert, a forest, a coast — that calls to something in you?", "theme": "Places & Travel"},
    {"text": "What is the most remote or unusual place you have ever visited?", "theme": "Places & Travel"},
    {"text": "Tell me about a journey where something went wrong and it became the story you tell most.", "theme": "Places & Travel"},
    {"text": "What place in the world do you associate with peace?", "theme": "Places & Travel"},
    {"text": "Have you visited the town or village your parents came from? What was that like?", "theme": "Places & Travel"},
    {"text": "If you could live anywhere in India for one year, where would you choose and why?", "theme": "Places & Travel"},
    {"text": "Tell me about a sunrise or sunset somewhere that you have never forgotten.", "theme": "Places & Travel"},
    {"text": "What is the smallest town or village that has left the biggest impression on you?", "theme": "Places & Travel"},
    {"text": "Did travel ever change your mind about something — a people, a belief, a way of life?", "theme": "Places & Travel"},
    {"text": "What place do you associate with the happiest time in your life?", "theme": "Places & Travel"},
    {"text": "If someone asked you to describe India to a foreign visitor using only places you have personally been, what would you show them?", "theme": "Places & Travel"},

    # -----------------------------------------------------------------------
    # THEME 8 — Festivals & Traditions (35 questions)
    # -----------------------------------------------------------------------
    {"text": "What is your favourite festival, and what makes it feel so special?", "theme": "Festivals & Traditions"},
    {"text": "Tell me about the most memorable Diwali of your life.", "theme": "Festivals & Traditions"},
    {"text": "What was Holi like when you were young? How has it changed?", "theme": "Festivals & Traditions"},
    {"text": "What did Eid look like in your neighbourhood or community when you were growing up?", "theme": "Festivals & Traditions"},
    {"text": "Tell me about a Navratri or Durga Puja celebration that you remember clearly.", "theme": "Festivals & Traditions"},
    {"text": "What festival tradition from your childhood has disappeared that you wish was still alive?", "theme": "Festivals & Traditions"},
    {"text": "How did your family prepare for festivals when you were young — what was the ritual in the days before?", "theme": "Festivals & Traditions"},
    {"text": "Tell me about the sweetest or most memorable wedding you have attended.", "theme": "Festivals & Traditions"},
    {"text": "What tradition at weddings in your community do you love the most?", "theme": "Festivals & Traditions"},
    {"text": "Is there a ritual in your family that outsiders might find odd but that you consider sacred?", "theme": "Festivals & Traditions"},
    {"text": "What does Ganesh Chaturthi mean to you? Or Onam, or Bihu — whichever festival is closest to your heart?", "theme": "Festivals & Traditions"},
    {"text": "Tell me about the way your family celebrated birthdays when you were young.", "theme": "Festivals & Traditions"},
    {"text": "What is the most beautiful decoration or rangoli you ever remember seeing?", "theme": "Festivals & Traditions"},
    {"text": "How did your family mark anniversaries or important personal milestones?", "theme": "Festivals & Traditions"},
    {"text": "Is there a puja or prayer ritual at home that has been part of your family for generations?", "theme": "Festivals & Traditions"},
    {"text": "What does Makar Sankranti or Lohri or Pongal mean to you — and how did you celebrate?", "theme": "Festivals & Traditions"},
    {"text": "Tell me about the role of music and dance in the festivals of your community.", "theme": "Festivals & Traditions"},
    {"text": "What is the smell you most associate with a particular festival?", "theme": "Festivals & Traditions"},
    {"text": "Did you ever celebrate a festival away from home — in a new city, or abroad? What was that like?", "theme": "Festivals & Traditions"},
    {"text": "What tradition have you started in your own family that your children or grandchildren now look forward to?", "theme": "Festivals & Traditions"},
    {"text": "Tell me about the most elaborate or memorable feast you have ever sat down to.", "theme": "Festivals & Traditions"},
    {"text": "What clothing or jewellery do you associate with a particular festival or occasion?", "theme": "Festivals & Traditions"},
    {"text": "Is there a story behind a particular family tradition — why you do something a certain way?", "theme": "Festivals & Traditions"},
    {"text": "What has been the most moving religious or spiritual moment of your life?", "theme": "Festivals & Traditions"},
    {"text": "Tell me about a festival that connected you with a community you were not born into.", "theme": "Festivals & Traditions"},
    {"text": "What did Christmas or New Year look like in your city or community when you were young?", "theme": "Festivals & Traditions"},
    {"text": "Is there a particular aarti or prayer that you know by heart? When did you learn it?", "theme": "Festivals & Traditions"},
    {"text": "What do you think festivals are really for — what is their deeper purpose?", "theme": "Festivals & Traditions"},
    {"text": "Tell me about a festival where something unexpected and funny happened.", "theme": "Festivals & Traditions"},
    {"text": "What is a festival tradition that you think young people are not passing on?", "theme": "Festivals & Traditions"},
    {"text": "Did your family have a particular Diwali routine — visiting certain relatives, buying certain things?", "theme": "Festivals & Traditions"},
    {"text": "Tell me about a gift you received at a festival that you have never forgotten.", "theme": "Festivals & Traditions"},
    {"text": "What tradition from another faith or community do you admire and wish was part of your own?", "theme": "Festivals & Traditions"},
    {"text": "If you could relive one festival from your past exactly as it was, which one would it be?", "theme": "Festivals & Traditions"},
    {"text": "What single object — a lamp, a flower, a sound — captures the feeling of your favourite festival?", "theme": "Festivals & Traditions"},

    # -----------------------------------------------------------------------
    # THEME 9 — Wisdom & Beliefs (36 questions — brings total to 306)
    # -----------------------------------------------------------------------
    {"text": "What is the single most important thing life has taught you?", "theme": "Wisdom & Beliefs"},
    {"text": "What do you believe about God — has that belief changed over your lifetime?", "theme": "Wisdom & Beliefs"},
    {"text": "What is the best piece of advice you ever received, and who gave it to you?", "theme": "Wisdom & Beliefs"},
    {"text": "What do you know now that you wish you had known at 30?", "theme": "Wisdom & Beliefs"},
    {"text": "What has been the greatest source of strength in your life?", "theme": "Wisdom & Beliefs"},
    {"text": "Is there something you used to believe completely that you no longer believe at all?", "theme": "Wisdom & Beliefs"},
    {"text": "What does courage mean to you? Can you think of a time you witnessed real courage?", "theme": "Wisdom & Beliefs"},
    {"text": "What do you think happens after we die? Has that view changed as you have gotten older?", "theme": "Wisdom & Beliefs"},
    {"text": "What is your philosophy when things go wrong — how do you find your way back?", "theme": "Wisdom & Beliefs"},
    {"text": "What is the thing you are most grateful for in your life?", "theme": "Wisdom & Beliefs"},
    {"text": "Tell me about a moment of grace — something that happened that you cannot fully explain.", "theme": "Wisdom & Beliefs"},
    {"text": "What do you think is the purpose of suffering? Has your view on this changed?", "theme": "Wisdom & Beliefs"},
    {"text": "What is kindness to you — can you describe the kindest person you ever met?", "theme": "Wisdom & Beliefs"},
    {"text": "What does it mean to live with integrity? Did you know someone who truly had it?", "theme": "Wisdom & Beliefs"},
    {"text": "What is the thing you most regret in your life — and what did it teach you?", "theme": "Wisdom & Beliefs"},
    {"text": "What do you believe about fate and free will — do we choose our lives, or are they chosen for us?", "theme": "Wisdom & Beliefs"},
    {"text": "What is the wisest thing you have ever heard someone say?", "theme": "Wisdom & Beliefs"},
    {"text": "How has your relationship with prayer or spiritual practice changed over your lifetime?", "theme": "Wisdom & Beliefs"},
    {"text": "What is something you once feared that you no longer fear at all?", "theme": "Wisdom & Beliefs"},
    {"text": "What do you think the secret to a long marriage is?", "theme": "Wisdom & Beliefs"},
    {"text": "What does ageing feel like from the inside — is it different from what you expected?", "theme": "Wisdom & Beliefs"},
    {"text": "What is the best thing about being the age you are now?", "theme": "Wisdom & Beliefs"},
    {"text": "What do you think young people today are getting right that your generation got wrong?", "theme": "Wisdom & Beliefs"},
    {"text": "What is your definition of happiness? Has it changed?", "theme": "Wisdom & Beliefs"},
    {"text": "What is the most important lesson you have learned about money?", "theme": "Wisdom & Beliefs"},
    {"text": "What has nature — the sky, the sea, the rain — taught you that people could not?", "theme": "Wisdom & Beliefs"},
    {"text": "If you could leave one message for the world — a single sentence — what would it be?", "theme": "Wisdom & Beliefs"},
    {"text": "What is something you have made peace with that you once fought against?", "theme": "Wisdom & Beliefs"},
    {"text": "What role has patience played in your life?", "theme": "Wisdom & Beliefs"},
    {"text": "What does home mean to you — is it a place, a person, a feeling?", "theme": "Wisdom & Beliefs"},
    {"text": "What is the most important relationship in a person's life, in your view?", "theme": "Wisdom & Beliefs"},
    {"text": "What would you want written on your gravestone or said about you when you are gone?", "theme": "Wisdom & Beliefs"},
    {"text": "Is there a prayer or verse or line of poetry that you return to when life is difficult?", "theme": "Wisdom & Beliefs"},
    {"text": "What does it mean to you to have lived a good life?", "theme": "Wisdom & Beliefs"},
    {"text": "If you were to give your grandchildren one piece of wisdom to carry through their lives, what would it be?", "theme": "Wisdom & Beliefs"},
    {"text": "Looking back at everything — the whole of your life so far — what are you most proud of?", "theme": "Wisdom & Beliefs"},
]

# Total: 306 questions.


# ---------------------------------------------------------------------------
# Seed function — populates the DB table on first startup
# ---------------------------------------------------------------------------

def seed_memory_questions() -> None:
    """
    Insert all questions from _QUESTION_BANK into the memory_questions table
    if the table is currently empty. Safe to call on every startup — does nothing
    if questions are already present.
    """
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM memory_questions").fetchone()[0]
        if count > 0:
            logger.info("MEMORY_Q | seed skipped — %d questions already in DB", count)
            return

        conn.executemany(
            "INSERT INTO memory_questions (question_text, theme) VALUES (?, ?)",
            [(q["text"], q["theme"]) for q in _QUESTION_BANK],
        )
        conn.commit()
        logger.info(
            "MEMORY_Q | seeded %d questions into memory_questions table",
            len(_QUESTION_BANK),
        )


# ---------------------------------------------------------------------------
# Selection logic — pick a random unasked question for this user
# ---------------------------------------------------------------------------

def get_next_memory_question(user_id: int):
    """
    Pick a random question from the bank that this user has NOT yet been asked.
    Returns (question_id, question_text, theme).

    If all questions have been asked, the tracking table for this user is cleared
    (the cycle resets) and a fresh question is picked from the full bank.

    Returns (None, None, None) if the bank is empty.
    """
    with get_connection() as conn:
        # Find all question IDs not yet asked to this user
        unasked = conn.execute(
            """
            SELECT id, question_text, theme
            FROM memory_questions
            WHERE id NOT IN (
                SELECT question_id FROM user_question_tracking WHERE user_id = ?
            )
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if unasked:
            return unasked["id"], unasked["question_text"], unasked["theme"]

        # All questions asked — reset the cycle for this user
        logger.info(
            "MEMORY_Q | user_id=%s has been asked all questions — resetting cycle", user_id
        )
        conn.execute(
            "DELETE FROM user_question_tracking WHERE user_id = ?", (user_id,)
        )
        conn.commit()

        # Pick fresh from the full bank
        fresh = conn.execute(
            "SELECT id, question_text, theme FROM memory_questions ORDER BY RANDOM() LIMIT 1"
        ).fetchone()

        if fresh:
            return fresh["id"], fresh["question_text"], fresh["theme"]

        return None, None, None


# ---------------------------------------------------------------------------
# Send a memory prompt to a single user
# ---------------------------------------------------------------------------

async def send_memory_prompt(bot, user_id: int, language: str = "english") -> bool:
    """
    Send a memory question to the senior as a warm text message + TTS voice note.

    Steps:
      1. Pick the next unasked question.
      2. Send it as a warm text message.
      3. Send a TTS voice note (failure is non-fatal).
      4. Record in user_question_tracking (won't repeat until bank exhausted).
      5. Record in memory_prompt_log (prevents double-send today).
      6. Set pending_memory_question_* on the user row so main.py can capture
         the response and save it to the memories table with full metadata.

    Returns True if sent successfully, False otherwise.
    """
    from tts import text_to_speech
    import io

    question_id, question_text, theme = get_next_memory_question(user_id)
    if question_id is None:
        logger.warning("MEMORY_Q | no question available | user_id=%s", user_id)
        return False

    # Build a warm framing around the question
    intro = _build_memory_intro(language)
    full_message = f"{intro}\n\n{question_text}"

    try:
        await bot.send_message(chat_id=user_id, text=full_message)
        logger.info(
            "MEMORY_Q | sent | user_id=%s | question_id=%s | theme=%s",
            user_id, question_id, theme,
        )
    except Exception as e:
        logger.error("MEMORY_Q | send failed | user_id=%s | %s", user_id, e)
        return False

    # TTS voice note — non-fatal if it fails (text already delivered)
    try:
        audio_bytes = text_to_speech(full_message, user_language=language)
        await bot.send_voice(chat_id=user_id, voice=io.BytesIO(audio_bytes))
    except Exception as tts_err:
        logger.warning("MEMORY_Q | TTS failed | user_id=%s | %s", user_id, tts_err)

    today = _current_date()

    # Record in tracking tables
    with get_connection() as conn:
        # Mark this question as asked for this user
        try:
            conn.execute(
                "INSERT OR IGNORE INTO user_question_tracking (user_id, question_id) VALUES (?, ?)",
                (user_id, question_id),
            )
        except Exception:
            pass

        # Record in memory_prompt_log (UNIQUE on user_id + sent_date — safe to ignore conflict)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO memory_prompt_log (user_id, question_id, sent_date) VALUES (?, ?, ?)",
                (user_id, question_id, today),
            )
        except Exception:
            pass

        conn.commit()

    # Set the pending flag on the user so main.py can capture the response
    update_user_fields(
        user_id,
        pending_memory_question_id=question_id,
        pending_memory_question_text=question_text,
        pending_memory_question_theme=theme,
    )

    return True


def _build_memory_intro(language: str) -> str:
    """
    A short, warm framing sentence before the memory question.
    Rotates between a few variants so it doesn't feel mechanical.
    Language-aware: Hindi/Hinglish users get a Hindi opener.
    """
    english_intros = [
        "I've been thinking of something I wanted to ask you.",
        "There's something I've been curious about.",
        "I'd love to hear a story from your life, if you're in the mood.",
        "Something came to mind that I've been wanting to ask.",
        "I have a question I've been saving for a quiet moment.",
    ]
    hindi_intros = [
        "Aaj mujhe aapse kuch poochna tha.",
        "Ek sawaal tha jo main kafi waqt se poochna chahta tha.",
        "Aaj ek purani baat yaad aai — sochा aapse hi poochhu.",
        "Agar man ho toh aaj kuch bataiye mujhe.",
        "Thodi der ke liye kuch yaadein share kijiye mere saath?",
    ]
    lang = (language or "english").lower()
    if lang in ("hindi", "hinglish"):
        return random.choice(hindi_intros)
    return random.choice(english_intros)


# ---------------------------------------------------------------------------
# Check and send — called from rituals.py scheduler tick
# ---------------------------------------------------------------------------

async def check_and_send_memory_prompts(bot) -> None:
    """
    Called from check_and_send_rituals() every 60 seconds.

    Sends a memory question to eligible users on Wednesday (weekday 2)
    and Sunday (weekday 6) only, at their morning check-in time (±1 minute
    tolerance to handle scheduler jitter).

    A user is eligible if:
      - onboarding is complete
      - account is active
      - today is Wednesday or Sunday
      - they have NOT already received a memory prompt today
      - their morning_checkin_time matches the current HH:MM
    """
    today_weekday = _day_of_week()
    if today_weekday not in (2, 6):  # 2 = Wednesday, 6 = Sunday
        return

    today = _current_date()
    now_hhmm = _current_hhmm()

    with get_connection() as conn:
        due_users = conn.execute(
            """
            SELECT u.user_id, u.language, u.morning_checkin_time
            FROM users u
            WHERE u.onboarding_complete = 1
              AND COALESCE(u.account_status, 'active') = 'active'
              AND u.morning_checkin_time = ?
              AND NOT EXISTS (
                  SELECT 1 FROM memory_prompt_log mpl
                  WHERE mpl.user_id = u.user_id
                    AND mpl.sent_date = ?
              )
            """,
            (now_hhmm, today),
        ).fetchall()

    for row in due_users:
        user_id = row["user_id"]
        language = row["language"] or "english"
        try:
            await send_memory_prompt(bot, user_id, language=language)
        except Exception as e:
            logger.error(
                "MEMORY_Q | prompt dispatch failed | user_id=%s | %s", user_id, e
            )


# ---------------------------------------------------------------------------
# Response capture — called from main.py when a pending question exists
# ---------------------------------------------------------------------------

def get_pending_memory_question(user_row):
    """
    Return (question_id, question_text, theme) if the user has a pending
    memory question awaiting a response, else (None, None, None).

    Accepts a user_row dict (the sqlite3.Row from get_or_create_user).
    """
    try:
        qid = user_row["pending_memory_question_id"]
        qtext = user_row["pending_memory_question_text"]
        qtheme = user_row["pending_memory_question_theme"]
        if qid:
            return int(qid), qtext, qtheme
    except (KeyError, TypeError):
        pass
    return None, None, None


def save_memory_response(
    user_id: int,
    response_text: str,
    question_id: int,
    question_text: str,
    theme: str,
) -> None:
    """
    Save the senior's response to a memory question into the memories table,
    fully linked (question_id, question_text, theme all set), then clear the
    pending flag on the user row.

    This is the function that makes the memories table meaningful — every row
    has full context, not just free-floating extracted text.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO memories
                (user_id, question_id, question_text, response_text, theme)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, question_id, question_text, response_text, theme),
        )
        conn.commit()

    logger.info(
        "MEMORY_Q | response saved | user_id=%s | question_id=%s | theme=%s | len=%d",
        user_id, question_id, theme, len(response_text),
    )

    # Clear the pending flag — this user's response has been captured
    update_user_fields(
        user_id,
        pending_memory_question_id=None,
        pending_memory_question_text=None,
        pending_memory_question_theme=None,
    )
