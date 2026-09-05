# 🌐 Colocar o dashboard na internet (para a equipe usar)

Objetivo: um link que suas funcionárias abrem de qualquer celular ou computador,
protegido por uma senha só. Hospedagem na **Streamlit Community Cloud** — grátis,
sem cartão de crédito.

---

## Etapa 1 — Criar a conta no GitHub (só você pode fazer, 2 minutos)

O GitHub é onde o **código** fica guardado (nunca o seu token nem a senha — isso vai
separado, na Etapa 3).

1. Abra https://github.com/signup
2. Crie a conta com o seu e-mail e confirme pelo link que chegar.

## Etapa 2 — Autorizar seu computador a enviar o código

No Terminal, rode:

```bash
gh auth login
```

Responda assim (use as setas e Enter):

| Pergunta | Resposta |
|---|---|
| What account do you want to log into? | **GitHub.com** |
| What is your preferred protocol...? | **HTTPS** |
| Authenticate Git with your GitHub credentials? | **Yes** |
| How would you like to authenticate? | **Login with a web browser** |

Ele mostra um código de 8 caracteres (tipo `A1B2-C3D4`). Copie, aperte Enter, o navegador
abre, cole o código e autorize. Quando voltar ao Terminal aparecendo `Logged in as ...`,
está pronto — me avise que eu faço o resto.

## Etapa 3 — (eu faço) Enviar o código e publicar

Eu crio o repositório **privado**, envio o código e te passo o link para conectar na
Streamlit Cloud, onde você cola os segredos:

```toml
TINY_TOKEN = "seu token do Tiny"
APP_SENHA = "a senha da equipe"
TINY_STATUS_VENDA = "Aprovado,Faturado"
TINY_PAUSA_SEGUNDOS = "0.4"
```

Esses valores ficam guardados na Streamlit Cloud, **não** no GitHub.

---

## Depois de publicado

- Você recebe um link do tipo `https://seu-app.streamlit.app`.
- Passe o link + a senha para as funcionárias. Elas abrem, digitam a senha e veem tudo.
- Para **trocar a senha**: painel da Streamlit Cloud → Settings → Secrets → mude o
  `APP_SENHA` → Save. Quem estava logado é deslogado na próxima vez que abrir.

### Uma coisa importante sobre o cache

Na nuvem, o cache de pedidos vive enquanto o app está de pé. Se o app ficar uns dias sem
ninguém abrir, ele "dorme" e, ao acordar, o cache volta vazio — aí alguém precisa clicar
em **🔄 Atualizar dados** de novo (um mês inteiro leva uns 10 minutos).

Se isso incomodar, dá para guardar o cache num banco de dados de verdade (Neon ou Supabase,
também grátis) e o problema some. É um passo a mais, que só vale a pena se acontecer muito.
