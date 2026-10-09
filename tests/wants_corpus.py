"""Invented sentences for measuring the "wants to own" search.

Every sentence is made up, in the registers a cover letter is written in.
WISH: what they would take on, said in their own way -- the card must find
it. NOT_A_WISH: what a narrow reading would wrongly take for one -- a past
fact, a politeness, a reason for liking the company, somebody else's wish, a
refusal -- the card must stay quiet.

The corpus was written by the same hand as the patterns, so it measures how
many of the phrasings someone thought of are covered, not the recall on real
letters. HELD_OUT was written before the patterns were widened and was not
used to tune them: it is the closer of the two to an honest measure.
"""

WISH = [
    # English, the phrasings the old pattern already knew
    "I'd want to own the treasury stack.",
    "I want to own the month-end close.",
    "I would like to build the reconciliation agent.",
    "I'd love to own strategy and operations end to end.",
    # English, the same wish said otherwise
    "What excites me most is building the payables workflow from scratch.",
    "I'm most excited about owning the investor reporting.",
    "I'd like to take on accounts payable automation.",
    "I'd love to build the hiring pipeline.",
    "My goal is to run finance operations without a spreadsheet in sight.",
    "I'm looking to own a product area end to end.",
    "I'm looking for a role where I can own the close.",
    "What I'd really like is to own onboarding for new customers.",
    "What interests me most is the tooling around the CFO office.",
    "I'm keen to take ownership of vendor payments.",
    "Ideally I would own the internal tooling.",
    "I'm eager to build the data layer your agents read from.",
    "The part I'd most want to own is treasury.",
    "I am hoping to take responsibility for payments operations.",
    "I see myself owning the operating layer within a year.",
    "Give me the close and I would make it a two-day job.",
    # French
    "J'aimerais prendre en charge la trésorerie.",
    "Ce qui m'intéresse le plus, c'est la finance opérationnelle.",
    "Je voudrais piloter les opérations financières.",
    "Mon objectif est de construire l'outil de rapprochement.",
    "Je souhaite reprendre le reporting aux investisseurs.",
    # Spanish
    "Me gustaría encargarme de la tesorería.",
    "Lo que más me interesa es la automatización de cuentas a pagar.",
    "Quiero liderar las operaciones financieras.",
    "Mi objetivo es construir el cierre mensual automático.",
    "Me encantaría construir el pipeline de contratación.",
]

NOT_A_WISH = [
    # a past fact, however much it talks about owning
    "I owned the close for two entities.",
    "I took ownership of the budgeting process after the migration.",
    "My goal was to cut the close to three days, and we did.",
    "I wanted to own the close, but the CFO kept it.",
    "I would have liked to stay longer at Talvera.",
    "What excited me most was the migration.",
    "Own the event-driven backbone processing 40 million messages a day.",
    "What I'm great at: finding the thing nobody owns and owning it.",
    # politeness
    "I'd like to thank you for reading this far.",
    "I would like to apply for the Founders' Associate role.",
    "I'm looking forward to hearing from you.",
    "I would love to hear from you.",
    "I'm excited to apply for this role.",
    "I want to highlight that the gap was not a sabbatical.",
    "I am hoping to hear back soon.",
    "J'aimerais vous remercier pour votre lecture.",
    "Je souhaite postuler au poste de Founders' Associate.",
    "Me gustaría agradecerles su tiempo.",
    "Quiero destacar que el hueco no fue un año sabático.",
    # why the company, not what they would take on
    "What draws me to Causa Prima specifically is the decision to put agents in the CFO office.",
    "What interests me about Causa Prima is the decision to start with treasury.",
    "I'm interested in the role because of the mission.",
    "Ce qui m'attire chez vous, c'est la clarté du produit.",
    # somebody else's wish, or a refusal
    "Customers want to own their data.",
    "The founders would like to automate the close.",
    "I don't want to own sales.",
    "I'd never want to run a team of fifty.",
    # a skills line, a self-description
    "Skills: ownership, strategy, operations, GTM.",
    "I am rigorous, fast-moving, deeply analytical, and I take extreme ownership of everything I touch.",
    "I'm exceptional at driving AI-first transformation with extreme ownership.",
]

#: Written before the patterns were widened; never used to tune them.
HELD_OUT_WISH = [
    "Hand me the AP inbox and I'll make it disappear.",
    "If I joined, the first thing I'd take over is the vendor onboarding.",
    "I hope to own the reporting pack within my first quarter.",
    "Where I could add the most is the treasury workflow, and that's what I'd go after.",
    "I'd be glad to take the hiring loop off the founders' plate.",
    "Je veux construire les outils internes de la finance.",
    "Busco un puesto donde pueda encargarme del cierre.",
    "My ambition would be to run the operating layer.",
]
HELD_OUT_NOT = [
    "I would like to introduce myself.",
    "I'd love to chat about the role.",
    "Last year I wanted to build a data team.",
    "I led the treasury project and owned its delivery.",
    "My manager wanted me to own the forecast.",
    "Me encantaría conocerles en persona.",
]

#: Written after the patterns, to break them. First measure: 3 wishes in 8
#: found, and 5 of 8 non-wishes wrongly read. Exclusions were then added for
#: what the five have in common (a habit in the past; "I want to be honest",
#: "I'd want to know more", "take this opportunity", "je veux bien") -- the
#: misses were left alone, and are listed in the test, not hidden.
STRESS_WISH = [
    "The close is what I'd sink my teeth into.",
    "Treasury is the area I would most like to make mine.",
    "I'm drawn to owning the AP workflow end to end.",
    "In my first 90 days I'd ship a working reconciliation agent.",
    "Ownership of the investor update is something I'd welcome.",
    "Ich würde gerne die Buchhaltung übernehmen.",
    "Me veo liderando la tesorería.",
    "Mon souhait serait de piloter les achats.",
]
STRESS_NOT = [
    "I would like to take this opportunity to thank you.",
    "I'd want to know more about the team first.",
    "We want to own the market, said our CEO.",
    "I want to be honest about the gap.",
    "Quiero que sepan que estoy disponible.",
    "Je veux bien répondre à vos questions.",
    "If I'd known, I would have built it sooner.",
    "Previously I'd take on the close every month.",
]
