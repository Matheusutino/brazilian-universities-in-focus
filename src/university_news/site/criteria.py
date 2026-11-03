"""What each annotation field and category means, shown on /criterios/.

The values are the ones allowed by config/schemas/university_clipping.json;
labels come from labels.py. Keep this file in sync with the contract.
"""

from __future__ import annotations

from typing import Any

from .labels import SDG_NAMES, label

# (field key, contract path, title, question it answers, multiple values?, definitions)
CRITERIA: tuple[dict[str, Any], ...] = (
    {
        "key": "content_type",
        "path": "classification.content_type",
        "title": "Tipo de conteúdo",
        "question": "Em que formato jornalístico a publicação foi escrita?",
        "multiple": False,
        "values": {
            "noticia": "Relato curto e factual de um acontecimento recente.",
            "reportagem": "Texto aprofundado, com contexto, várias fontes e apuração própria.",
            "entrevista": "Publicação centrada em perguntas e respostas ou na fala de uma pessoa.",
            "opiniao": "Coluna ou artigo assinado que defende um ponto de vista.",
            "agenda": "Divulgação de um evento futuro, com data, local e programação.",
            "comunicado": "Aviso institucional ou administrativo: editais, prazos, matrículas, resultados, notas oficiais.",
            "outro": "Formato que não se encaixa nos anteriores.",
        },
    },
    {
        "key": "news_category",
        "path": "classification.news_category",
        "title": "Categoria da notícia",
        "question": "Qual é o assunto principal da publicação?",
        "multiple": False,
        "values": {
            "resultado_de_pesquisa": "Divulga descobertas ou resultados de uma pesquisa.",
            "projeto_de_pesquisa": "Apresenta um projeto: objetivos, equipe, financiamento ou início.",
            "evento": "Congresso, seminário, palestra, festival, cerimônia ou outra atividade com data.",
            "premiacao": "Prêmio, homenagem ou reconhecimento recebido por pessoas ou grupos.",
            "oportunidade": "Vagas, bolsas, editais, inscrições, processos seletivos ou cursos abertos.",
            "parceria": "Acordo, convênio ou cooperação com outras instituições, empresas ou governo.",
            "divulgacao_cientifica": "Explica um tema científico ao público, sem anunciar um resultado próprio.",
            "posicionamento_de_especialista": "Docente ou pesquisador comenta um tema da atualidade.",
            "institucional": "Gestão, administração, obras, eleições internas e vida da universidade.",
            "outro": "Assunto que não se encaixa nas categorias anteriores.",
        },
    },
    {
        "key": "institutional_axes",
        "path": "classification.institutional_axes",
        "title": "Eixos institucionais",
        "question": "Com quais missões da universidade a notícia se relaciona?",
        "multiple": True,
        "values": {
            "ensino": "Graduação, pós-graduação, cursos e formação de estudantes.",
            "pesquisa": "Produção de conhecimento científico.",
            "extensao": "Ações com e para a comunidade externa à universidade.",
            "inovacao": "Tecnologia, patentes, empreendedorismo e transferência de conhecimento.",
            "gestao": "Administração, orçamento, governança e infraestrutura.",
            "internacionalizacao": "Mobilidade, cooperação e presença internacional.",
            "cultura": "Artes, patrimônio e programação cultural.",
            "assistencia_estudantil": "Permanência estudantil: auxílios, moradia, alimentação, saúde.",
            "outro": "Eixo que não se encaixa nos anteriores.",
        },
    },
    {
        "key": "scientific_content",
        "path": "classification.scientific_content",
        "title": "Centralidade do conteúdo científico",
        "question": "Quanto da notícia é sobre ciência e pesquisa?",
        "multiple": False,
        "values": {
            "central": "A notícia trata essencialmente de ciência ou pesquisa.",
            "relevant": "Há conteúdo científico importante, mas ele não é o foco.",
            "peripheral": "A pesquisa aparece só de passagem.",
            "absent": "Não há conteúdo científico.",
        },
    },
    {
        "key": "scientific_fields",
        "path": "topics.scientific_fields",
        "title": "Grandes áreas do conhecimento (CNPq)",
        "question": "A quais grandes áreas da tabela do CNPq o tema pertence?",
        "multiple": True,
        "values": {
            "ciencias_exatas_e_da_terra": "Matemática, estatística, computação, física, química e geociências.",
            "ciencias_biologicas": "Biologia, genética, ecologia, microbiologia e áreas afins.",
            "engenharias": "Todas as engenharias.",
            "ciencias_da_saude": "Medicina, enfermagem, odontologia, farmácia, nutrição, saúde coletiva e afins.",
            "ciencias_agrarias": "Agronomia, veterinária, zootecnia, florestas, pesca e alimentos.",
            "ciencias_sociais_aplicadas": "Direito, administração, economia, comunicação, arquitetura, serviço social e afins.",
            "ciencias_humanas": "Filosofia, história, educação, psicologia, sociologia, geografia e afins.",
            "linguistica_letras_e_artes": "Linguística, literatura e artes.",
            "multidisciplinar": "Temas que atravessam várias áreas sem predominância de uma.",
        },
    },
    {
        "key": "geographic_scope",
        "path": "context.geographic_scope",
        "title": "Abrangência geográfica",
        "question": "Até onde chega o alcance do que é noticiado?",
        "multiple": False,
        "values": {
            "local": "Campus ou cidade.",
            "regional": "Estado ou região do país.",
            "national": "Brasil como um todo.",
            "international": "Envolve outros países.",
            "indeterminate": "Não é possível dizer pelo texto.",
        },
    },
    {
        "key": "stage",
        "path": "research.stage",
        "title": "Estágio da pesquisa",
        "question": "Em que ponto está a pesquisa mencionada?",
        "multiple": False,
        "values": {
            "proposal": "Proposta ou projeto aprovado, ainda não iniciado.",
            "in_progress": "Pesquisa em andamento, sem resultados divulgados.",
            "preliminary_results": "Primeiros resultados, ainda parciais.",
            "completed": "Pesquisa concluída, com resultados.",
            "not_applicable": "A notícia não trata de pesquisa.",
        },
    },
    {
        "key": "collaboration_types",
        "path": "research.collaboration_types",
        "title": "Tipos de colaboração",
        "question": "Com quem a universidade colabora na iniciativa?",
        "multiple": True,
        "values": {
            "inter_university": "Outras universidades ou instituições de pesquisa.",
            "government": "Órgãos de governo.",
            "industry": "Empresas.",
            "civil_society": "Organizações da sociedade civil e comunidades.",
            "international": "Parceiros de outros países.",
        },
    },
    {
        "key": "dimensions",
        "path": "societal_impact.dimensions",
        "title": "Dimensões de impacto social",
        "question": "Em que áreas da sociedade a iniciativa pode ter efeito?",
        "multiple": True,
        "values": {
            "health": "Saúde.",
            "education": "Educação.",
            "environment": "Meio ambiente.",
            "economy": "Economia e trabalho.",
            "technology": "Tecnologia.",
            "culture": "Cultura.",
            "social": "Inclusão, direitos e questões sociais.",
            "public_policy": "Políticas públicas.",
        },
    },
    {
        "key": "audience",
        "path": "communication.audience",
        "title": "Público principal",
        "question": "A quem a publicação se dirige em primeiro lugar?",
        "multiple": False,
        "values": {
            "general_public": "Qualquer leitor.",
            "scientific_community": "Pesquisadores e especialistas.",
            "students": "Estudantes, atuais ou futuros.",
            "government": "Gestores e órgãos públicos.",
            "industry": "Empresas.",
            "other": "Outro público específico.",
        },
    },
    {
        "key": "tone",
        "path": "communication.tone",
        "title": "Tom",
        "question": "Como o texto se posiciona diante do que relata?",
        "multiple": False,
        "values": {
            "informative": "Relata fatos de forma objetiva.",
            "promotional": "Valoriza a instituição ou a iniciativa, com linguagem de divulgação.",
            "educational": "Explica conceitos para que o leitor aprenda algo.",
            "celebratory": "Comemora uma conquista.",
            "critical": "Questiona ou aponta problemas.",
            "neutral": "Sem posicionamento perceptível.",
        },
    },
    {
        "key": "hype",
        "path": "communication.hype",
        "title": "Hype",
        "question": "Há exagero ou sensacionalismo na forma de comunicar?",
        "multiple": False,
        "values": {
            "none": "Linguagem sóbria, proporcional ao que é relatado.",
            "low": "Algum entusiasmo, sem distorcer os fatos.",
            "medium": "Superlativos ou promessas além do que o texto sustenta.",
            "high": "Sensacionalismo: afirma muito mais do que os fatos permitem.",
        },
    },
)

SCALES: tuple[dict[str, Any], ...] = (
    {
        "key": "accessibility",
        "path": "communication.accessibility_0_4",
        "title": "Acessibilidade da linguagem (0 a 4)",
        "question": "Um leitor sem formação na área entende o texto?",
        "levels": (
            "Só especialistas entendem.",
            "Muito técnico; termos sem explicação.",
            "Exige algum conhecimento prévio.",
            "Compreensível para a maioria dos leitores.",
            "Totalmente acessível, com termos explicados.",
        ),
    },
    {
        "key": "scientific_depth",
        "path": "communication.scientific_depth_0_4",
        "title": "Profundidade científica (0 a 4)",
        "question": "Quanto o texto explica da ciência envolvida?",
        "levels": (
            "Nenhuma explicação científica.",
            "Menciona o tema sem explicar.",
            "Explica o problema ou o resultado.",
            "Explica problema, método e resultado.",
            "Explica também limitações e contexto do conhecimento existente.",
        ),
    },
)


def criteria_for_page() -> list[dict[str, Any]]:
    """Criteria with Portuguese labels attached to every value."""
    return [
        criterion
        | {
            "options": [
                {"value": value, "label": label(criterion["key"], value), "definition": definition}
                for value, definition in criterion["values"].items()
            ]
        }
        for criterion in CRITERIA
    ]


def sdg_list() -> list[tuple[int, str]]:
    return sorted(SDG_NAMES.items())
