from google import genai
from config import GEMINI_KEY

print("Chave carregada:", bool(GEMINI_KEY))
print("Tamanho da chave:", len(GEMINI_KEY) if GEMINI_KEY else 0)

cliente = genai.Client(api_key=GEMINI_KEY)

resposta = cliente.models.generate_content(
    model="gemini-3.6-flash",
    contents="Responda apenas: API funcionando!"
)

print("RESPOSTA:")
print(resposta.text)