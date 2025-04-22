This is application used to retrive memes from internet and upload them into telegram chats.
find @meme_internet_getter_spamer_bot in telegram to start using bot
by command /start - the bot asking you to register. The only information it takes - is you chat_id (if you ran from the chat) and user_id
this information is public and doesn't use anywhere accept our application.
After registration you can get meme. By /start - GetMeme
or by command /and_meme
after that you suggest to get AnyMeme. It will take random meme and upload into your chat (or in bot chat if you working with bot directly).
It take meme usually from meme-api
if you chose "ByKeyWords" - bot will ask you then to type key words. Then it will download the meme from the services: reddit, giphy, pikabu
and upload into the chat.
third button about the language. If you choose - "Русский Язык" - the bot will try to find memes by key words firstly on russian services (pikabu) or
in reddit, in pages, related to russian memes. If your choose "English version" - it will do vice versa. (notice. Language button in /start command - not 
for this cases. It sets default language for your chat/user and just changes the bot messages from english to russian and vice versa)
We will goin to update this. Firstly, we want to use pininterest API to improve searching by keywords algoritm
