# Jornais New

Pipeline em três estágios para coletar notícias de portais universitários. No
momento estão configurados o Jornal da USP, o Notícias da Ufal, o Jornal da
Unesp, o Notícias da UnB, a UFMS, a UFRGS, a UFPA, a UFPR, a UFRJ, a UERJ, a UFABC, a UFMG, a UFLA, a Unicamp, a Unifesp, a UEM, a FURG e a UFC:

1. descoberta e deduplicação das URLs das páginas de listagem;
2. download assíncrono dos HTMLs crus, com concorrência e retries limitados;
3. extração estruturada com trafilatura e gravação em JSONL.

## Instalação

Requer Python 3.12 ou mais recente.

```bash
uv sync
```

Copie o arquivo de exemplo e informe sua chave do OpenRouter:

```bash
cp .env.example .env
```

```dotenv
OPENROUTER_API_KEY=your_openrouter_api_key_here
OPENROUTER_MODEL=openai/gpt-4o-mini
```

A CLI carrega `.env` automaticamente. O arquivo local é ignorado pelo Git.

## Uso por estágio

Os três estágios podem ser executados separadamente. Todos devem usar o mesmo
diretório de saída.

### 1. Descobrir e salvar as URLs

```bash
uv run python -m jornais_new usp \
  --stage discovery \
  --start-page 1 \
  --end-page 3579 \
  --output data/usp
```

Esse estágio cria `urls.jsonl` e `discovery.json`, sem baixar o conteúdo das
matérias.

Para a Ufal, a paginação usa offsets de 30 em 30. A coleta completa vai de 0 a
23.880:

```bash
uv run python -m jornais_new ufal \
  --stage discovery \
  --start-offset 0 \
  --end-offset 23880 \
  --output data/ufal
```

Para testar somente os primeiros 60 itens, use `--end-offset 30`.

Para a Unesp, a paginação vai de 1 a 136:

```bash
uv run python -m jornais_new unesp \
  --stage discovery \
  --start-page 1 \
  --end-page 136 \
  --output data/unesp
```

Para a UnB, a paginação usa offsets de 10 em 10. A coleta completa configurada
vai de 0 a 2.420:

```bash
uv run python -m jornais_new unb \
  --stage discovery \
  --start-offset 0 \
  --end-offset 2420 \
  --output data/unb
```

Para a UFMS, a paginação vai de 1 a 626:

```bash
uv run python -m jornais_new ufms \
  --stage discovery \
  --start-page 1 \
  --end-page 626 \
  --output data/ufms
```

Para a UFRGS, a última página válida verificada é a 2.667; a página seguinte
responde com `404`:

```bash
uv run python -m jornais_new ufrgs \
  --stage discovery \
  --start-page 1 \
  --end-page 2667 \
  --output data/ufrgs
```

Para a UFPA, a paginação vai de 1 a 809. A página 810 responde, mas não contém
mais resultados:

```bash
uv run python -m jornais_new ufpa \
  --stage discovery \
  --start-page 1 \
  --end-page 809 \
  --output data/ufpa
```

Para a UFPR, a paginação vai de 1 a 2.170:

```bash
uv run python -m jornais_new ufpr \
  --stage discovery \
  --start-page 1 \
  --end-page 2170 \
  --output data/ufpr
```

Para a UFRJ, a paginação vai de 1 a 1.789:

```bash
uv run python -m jornais_new ufrj \
  --stage discovery \
  --start-page 1 \
  --end-page 1789 \
  --output data/ufrj
```

Para a UERJ, a paginação vai de 1 a 139:

```bash
uv run python -m jornais_new uerj \
  --stage discovery \
  --start-page 1 \
  --end-page 139 \
  --output data/uerj
```

Para a UFABC, a paginação usa offsets de 10 em 10, de 0 a 2.080:

```bash
uv run python -m jornais_new ufabc \
  --stage discovery \
  --start-page 0 \
  --end-page 2080 \
  --output data/ufabc
```

Para a UFMG, a paginação vai de 1 a 259:

```bash
uv run python -m jornais_new ufmg \
  --stage discovery \
  --start-page 1 \
  --end-page 259 \
  --output data/ufmg
```

Para a UFLA, a paginação usa offsets de 12 em 12, de 0 a 4.032:

```bash
uv run python -m jornais_new ufla \
  --stage discovery \
  --start-page 0 \
  --end-page 4032 \
  --output data/ufla
```

Para a Unicamp, a paginação vai de 1 a 59:

```bash
uv run python -m jornais_new unicamp \
  --stage discovery \
  --start-page 1 \
  --end-page 59 \
  --output data/unicamp
```

Para a Unifesp, a paginação usa offsets de 4 em 4, de 0 a 432:

```bash
uv run python -m jornais_new unifesp \
  --stage discovery \
  --start-page 0 \
  --end-page 432 \
  --output data/unifesp
```

Para a UEM, a paginação usa offsets de 15 em 15, de 0 a 345:

```bash
uv run python -m jornais_new uem \
  --stage discovery \
  --start-page 0 \
  --end-page 345 \
  --output data/uem
```

Para a FURG, a paginação usa offsets de 20 em 20, de 0 a 25.780:

```bash
uv run python -m jornais_new furg \
  --stage discovery \
  --start-page 0 \
  --end-page 25780 \
  --output data/furg
```

Para a UFC, a paginação vai de 1 a 56:

```bash
uv run python -m jornais_new ufc \
  --stage discovery \
  --start-page 1 \
  --end-page 56 \
  --output data/ufc
```

### 2. Baixar os HTMLs das URLs

```bash
uv run python -m jornais_new usp \
  --stage fetching \
  --output data/usp
```

Esse estágio lê `urls.jsonl` e cria os arquivos em `raw/` e o `manifest.jsonl`.

```bash
uv run python -m jornais_new ufal --stage fetching --output data/ufal
uv run python -m jornais_new unb --stage fetching --output data/unb
uv run python -m jornais_new ufms --stage fetching --output data/ufms
uv run python -m jornais_new ufrgs --stage fetching --output data/ufrgs
uv run python -m jornais_new ufpa --stage fetching --output data/ufpa
uv run python -m jornais_new ufpr --stage fetching --output data/ufpr
uv run python -m jornais_new ufrj --stage fetching --output data/ufrj
uv run python -m jornais_new uerj --stage fetching --output data/uerj
uv run python -m jornais_new ufabc --stage fetching --output data/ufabc
uv run python -m jornais_new ufmg --stage fetching --output data/ufmg
uv run python -m jornais_new ufla --stage fetching --output data/ufla
uv run python -m jornais_new unicamp --stage fetching --output data/unicamp
uv run python -m jornais_new unifesp --stage fetching --output data/unifesp
uv run python -m jornais_new uem --stage fetching --output data/uem
uv run python -m jornais_new furg --stage fetching --output data/furg
uv run python -m jornais_new ufc --stage fetching --output data/ufc
```

### 3. Extrair o conteúdo estruturado

```bash
uv run python -m jornais_new usp \
  --stage extraction \
  --output data/usp
```

Esse estágio lê `urls.jsonl` e os HTMLs em `raw/`, produzindo `articles.jsonl`.
Cada registro contém `url`, `title`, `subtitle`, `author`, `date`, `text`, `tags`,
`links` e `images`. Os dois últimos campos guardam listas deduplicadas de URLs
absolutas encontradas somente no corpo editorial identificado pelo trafilatura.

Na Ufal, o corpo é delimitado por `article#content`; autor, data e palavras-chave
são complementados diretamente dos seletores estruturados do portal.

```bash
uv run python -m jornais_new ufal --stage extraction \
  --extractor trafilatura --output data/ufal
```

Na Unesp, título, subtítulo, autor, data e tags também são complementados pelos
seletores próprios do tema WordPress:

```bash
uv run python -m jornais_new unesp --stage fetching --output data/unesp
uv run python -m jornais_new unesp --stage extraction \
  --extractor trafilatura --output data/unesp
```

Na UnB, o corpo da página é delimitado por `.item-page`; título, subtítulo, autor,
data e tags são complementados pelos seletores do tema Joomla:

```bash
uv run python -m jornais_new unb --stage extraction \
  --extractor trafilatura --output data/unb
```

Na UFMS, o corpo é delimitado por `.post-inner-content`; título, data e categorias
são complementados pelos seletores do tema WordPress:

```bash
uv run python -m jornais_new ufms --stage extraction \
  --extractor trafilatura --output data/ufms
```

Na UFRGS, o corpo é delimitado por `.box-text-blog`; título e data são
complementados diretamente do HTML:

```bash
uv run python -m jornais_new ufrgs --stage extraction \
  --extractor trafilatura --output data/ufrgs
```

Na UFPA, o template Elementor delimita título, data, crédito, categorias, imagem
destacada e corpo editorial:

```bash
uv run python -m jornais_new ufpa --stage extraction \
  --extractor trafilatura --output data/ufpa
```

Na UFPR, o corpo editorial fica em `.single__post__text`; título, autor, data e
categoria são complementados pelo bloco de informações da notícia:

```bash
uv run python -m jornais_new ufpr --stage extraction \
  --extractor trafilatura --output data/ufpr
```

Na UFRJ, o corpo editorial fica em `.entry-content`, com título, subtítulo,
autor, data e editoria complementados pelos metadados do tema WordPress:

```bash
uv run python -m jornais_new ufrj --stage extraction \
  --extractor trafilatura --output data/ufrj
```

Na UERJ, o corpo editorial fica em `main#main .entry-content`; título, autor e
data são complementados pelo cabeçalho da notícia.

```bash
uv run python -m jornais_new uerj \
  --stage extraction \
  --extractor trafilatura --output data/uerj
```

Na UFABC, o corpo fica em `.item-page`; o título, a data de publicação e o
crédito institucional são complementados pelos campos do portal Joomla:

```bash
uv run python -m jornais_new ufabc \
  --stage extraction \
  --extractor trafilatura --output data/ufabc
```

Na UFMG, o corpo editorial fica em `main#primary > div.space-y-8`; título,
subtítulo, autor, data, tópicos e categoria são complementados pelos metadados
do portal:

```bash
uv run python -m jornais_new ufmg \
  --stage extraction \
  --extractor trafilatura --output data/ufmg
```

Na Unicamp, o corpo fica em `.single-post__post-content`; título, subtítulo,
autoria, data e categorias são complementados pelos metadados da página:

```bash
uv run python -m jornais_new unicamp \
  --stage extraction \
  --extractor trafilatura --output data/unicamp
```

Na Unifesp, o corpo fica em `.com-content-article__body`; título, subtítulo,
autoria, data e categoria são complementados pelos metadados do template Joomla:

```bash
uv run python -m jornais_new unifesp \
  --stage extraction \
  --extractor trafilatura --output data/unifesp
```

Na UEM, o corpo editorial fica em `#t3-content article.article-content`; título,
subtítulo, autor, data e tags são complementados pelo template Joomla:

```bash
uv run python -m jornais_new uem \
  --stage extraction \
  --extractor trafilatura --output data/uem
```

Na FURG, o corpo fica em `article.pagina .pagina__conteudo`; título, linha de
apoio, autor, data e assuntos são complementados pelos campos da notícia:

```bash
uv run python -m jornais_new furg \
  --stage extraction \
  --extractor trafilatura --output data/furg
```

Na UFC, o corpo fica em `.article .post-text`; título, subtítulo, autor, data e
categoria são complementados pelos campos do tema WordPress:

```bash
uv run python -m jornais_new ufc \
  --stage extraction \
  --extractor trafilatura --output data/ufc
```

Na UFLA, o corpo fica em `.item-page`; título, autor, data e categoria são
complementados pelos campos do template Joomla:

```bash
uv run python -m jornais_new ufla \
  --stage extraction \
  --extractor trafilatura --output data/ufla
```

O extrator pode ser selecionado explicitamente:

```bash
# Determinístico, local e sem custo de API (padrão)
uv run python -m jornais_new usp --stage extraction \
  --extractor trafilatura --output data/usp --force

# Envia todos os artigos ao Crawl4AI + OpenRouter
uv run python -m jornais_new usp --stage extraction \
  --extractor llm --output data/usp --force

# Usa OpenRouter somente se título, autor, data ou texto adequado estiver ausente
uv run python -m jornais_new usp --stage extraction \
  --extractor hybrid --output data/usp --force
```

Os modos `llm` e `hybrid` processam os HTMLs já salvos; eles não baixam novamente
as páginas. O modo híbrido mantém o resultado do trafilatura se o OpenRouter
falhar, registrando um aviso no log. Use `--force` ao trocar de extrator para não
reutilizar registros gerados anteriormente.

### Executar o pipeline completo

```bash
uv run python -m jornais_new usp --stage all --output data/usp
```

`all` é o padrão, então `--stage all` pode ser omitido. A faixa padrão da USP é de
1 a 3.579. Por padrão, artefatos existentes são reutilizados. `--force` refaz
somente o estágio selecionado, e `--concurrency` pode reduzir o limite de dez
requisições simultâneas.

## Dashboard

O dashboard detecta automaticamente as universidades presentes em `data/` e
consolida os arquivos JSONL com DuckDB. Ele apresenta o progresso dos três
estágios, pesquisa e leitura de artigos, qualidade dos campos e erros:

```bash
uv run streamlit run dashboard/app.py
```

Por padrão, a interface fica disponível em `http://localhost:8501`. O botão
**Atualizar dados** recarrega os artefatos após uma nova execução do pipeline.
Na aba **Visão geral**, os jornais são agrupados pelas cinco regiões do Brasil;
a barra lateral permite filtrar por região e por universidade. O mapeamento atual
é: UFPA (Norte), UFAL (Nordeste), UnB e UFMS (Centro-Oeste), USP e Unesp
(Sudeste), e UFRGS, UFPR, UEM e FURG (Sul). A UFRJ, a UERJ, a UFABC, a UFMG, a UFLA, a Unicamp e a Unifesp também ficam no Sudeste. A UFC fica no Nordeste.

## Artefatos

- `urls.jsonl`: URLs descobertas, únicas e ordenadas;
- `discovery.json`: faixa de páginas associada à descoberta;
- `raw/*.html`: respostas HTML antes da extração;
- `manifest.jsonl`: relação entre URL, arquivo, status e horário do download;
- `articles.jsonl`: registros com texto, metadados, `links` e `images` do corpo;
- `errors.jsonl`: falhas individuais da execução mais recente.

Falhas individuais não interrompem o lote. Uma nova execução retoma os downloads e
as extrações pendentes.

## Testes

```bash
uv run pytest
```

Este projeto usa [Crawl4AI](https://github.com/unclecode/crawl4ai) para extração
estruturada opcional com LLM.
