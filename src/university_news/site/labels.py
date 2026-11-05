"""Portuguese labels for the enrichment contract's enum values."""

from __future__ import annotations

from typing import Any

import numpy as np

LABELS: dict[str, dict[str, str]] = {
    "content_type": {
        "noticia": "Notícia",
        "reportagem": "Reportagem",
        "entrevista": "Entrevista",
        "opiniao": "Opinião",
        "agenda": "Agenda",
        "comunicado": "Comunicado",
        "outro": "Outro",
    },
    "news_category": {
        "resultado_de_pesquisa": "Resultado de pesquisa",
        "projeto_de_pesquisa": "Projeto de pesquisa",
        "evento": "Evento",
        "premiacao": "Premiação",
        "oportunidade": "Oportunidade",
        "parceria": "Parceria",
        "divulgacao_cientifica": "Divulgação científica",
        "posicionamento_de_especialista": "Especialista comenta",
        "institucional": "Institucional",
        "outro": "Outro",
    },
    "institutional_axes": {
        "ensino": "Ensino",
        "pesquisa": "Pesquisa",
        "extensao": "Extensão",
        "inovacao": "Inovação",
        "gestao": "Gestão",
        "internacionalizacao": "Internacionalização",
        "cultura": "Cultura",
        "assistencia_estudantil": "Assistência estudantil",
        "outro": "Outro",
    },
    "scientific_content": {
        "central": "Central",
        "relevant": "Relevante",
        "peripheral": "Periférico",
        "absent": "Ausente",
    },
    "scientific_fields": {
        "ciencias_exatas_e_da_terra": "Exatas e da Terra",
        "ciencias_biologicas": "Biológicas",
        "engenharias": "Engenharias",
        "ciencias_da_saude": "Saúde",
        "ciencias_agrarias": "Agrárias",
        "ciencias_sociais_aplicadas": "Sociais aplicadas",
        "ciencias_humanas": "Humanas",
        "linguistica_letras_e_artes": "Linguística, letras e artes",
        "multidisciplinar": "Multidisciplinar",
    },
    "geographic_scope": {
        "local": "Local",
        "regional": "Regional",
        "national": "Nacional",
        "international": "Internacional",
        "indeterminate": "Indeterminado",
    },
    "stage": {
        "proposal": "Proposta",
        "in_progress": "Em andamento",
        "preliminary_results": "Resultados preliminares",
        "completed": "Concluída",
        "not_applicable": "Não se aplica",
    },
    "collaboration_types": {
        "inter_university": "Entre universidades",
        "government": "Governo",
        "industry": "Indústria",
        "civil_society": "Sociedade civil",
        "international": "Internacional",
    },
    "dimensions": {
        "health": "Saúde",
        "education": "Educação",
        "environment": "Meio ambiente",
        "economy": "Economia",
        "technology": "Tecnologia",
        "culture": "Cultura",
        "social": "Social",
        "public_policy": "Políticas públicas",
    },
    "audience": {
        "general_public": "Público geral",
        "scientific_community": "Comunidade científica",
        "students": "Estudantes",
        "government": "Governo",
        "industry": "Indústria",
        "other": "Outro",
    },
    "tone": {
        "informative": "Informativo",
        "promotional": "Promocional",
        "educational": "Educativo",
        "celebratory": "Celebrativo",
        "critical": "Crítico",
        "neutral": "Neutro",
    },
    "hype": {"none": "Nenhum", "low": "Baixo", "medium": "Médio", "high": "Alto"},
    "type": {
        "person": "Pessoa",
        "university": "Universidade",
        "research_group": "Grupo de pesquisa",
        "company": "Empresa",
        "government_agency": "Órgão público",
        "funding_agency": "Agência de fomento",
        "other": "Outro",
    },
    "role": {
        "researcher": "Pesquisador(a)",
        "student": "Estudante",
        "expert": "Especialista",
        "author": "Autor(a)",
        "organizer": "Organizador(a)",
        "partner": "Parceiro",
        "funder": "Financiador",
        "other": "Outro",
    },
}

SDG_NAMES = {
    1: "Erradicação da pobreza",
    2: "Fome zero",
    3: "Saúde e bem-estar",
    4: "Educação de qualidade",
    5: "Igualdade de gênero",
    6: "Água potável e saneamento",
    7: "Energia limpa",
    8: "Trabalho decente",
    9: "Indústria e inovação",
    10: "Redução das desigualdades",
    11: "Cidades sustentáveis",
    12: "Consumo responsável",
    13: "Ação climática",
    14: "Vida na água",
    15: "Vida terrestre",
    16: "Paz e justiça",
    17: "Parcerias",
}


def label(group: str, value: object) -> str:
    """Portuguese label for an enum value; free text is returned unchanged."""
    text = str(value)
    if group not in LABELS:
        return text
    return LABELS[group].get(text, text.replace("_", " ").capitalize())


def as_list(value: object) -> list[Any]:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return []
