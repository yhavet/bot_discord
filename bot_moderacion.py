import discord
from discord import app_commands
from discord.ext import commands
from collections import defaultdict
from dotenv import load_dotenv
import datetime
import sys
import os

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CANAL_CONFIGURADO_ID = 1395584253097672726
ROL_PROTEGIDO = "creador"
MAX_DENUNCIAS_POR_USUARIO = 1

NIVELES = [
    {"umbral": 3,    "duracion": datetime.timedelta(minutes=30), "etiqueta": "30 minutos"},
    {"umbral": 50,   "duracion": datetime.timedelta(hours=1),    "etiqueta": "1 hora"},
    {"umbral": 150,  "duracion": datetime.timedelta(days=1),     "etiqueta": "1 dia"},
    {"umbral": 1500, "duracion": datetime.timedelta(weeks=1),    "etiqueta": "1 semana"},
]

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.tree.add_command(menu_denuncia)
        await self.tree.sync()

    async def on_ready(self):
        print(f"Bot conectado como {self.user} (ID: {self.user.id})")

bot = MyBot()

denuncias = defaultdict(lambda: defaultdict(int))
denuncias_por_usuario = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
historial_sanciones = defaultdict(lambda: defaultdict(list))

def tiene_rol_protegido(miembro: discord.Member) -> bool:
    return any(rol.name.lower() == ROL_PROTEGIDO.lower() for rol in miembro.roles)

def obtener_nivel_actual(total: int):
    nivel_actual = None
    for nivel in NIVELES:
        if total >= nivel["umbral"]:
            nivel_actual = nivel
    return nivel_actual

def obtener_proximo_umbral(total: int):
    for nivel in NIVELES:
        if total < nivel["umbral"]:
            return nivel
    return None

def contar_sanciones_acumuladas(guild_id, user_id, nivel_umbral):
    registros = historial_sanciones[guild_id][user_id]
    return sum(1 for r in registros if r["umbral"] == nivel_umbral)

def limpiar_historial_expirado(guild_id, user_id):
    ahora = datetime.datetime.utcnow()
    historial_sanciones[guild_id][user_id] = [
        r for r in historial_sanciones[guild_id][user_id] if ahora < r["expira"]
    ]

def formatear_duracion(duracion: datetime.timedelta) -> str:
    dias = duracion.days
    horas = duracion.seconds // 3600
    minutos = (duracion.seconds % 3600) // 60
    partes = []
    if dias: partes.append(f"{dias} dia(s)")
    if horas: partes.append(f"{horas} hora(s)")
    if minutos: partes.append(f"{minutos} minuto(s)")
    return ", ".join(partes) if partes else "menos de 1 minuto"

@app_commands.context_menu(name="Denunciar mensaje")
async def menu_denuncia(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.defer(ephemeral=True)

    acusado = message.author
    denunciante = interaction.user

    if not interaction.guild:
        await interaction.followup.send("Este comando solo funciona en servidores.", ephemeral=True)
        return

    if acusado.bot:
        await interaction.followup.send("No podes denunciar a un bot.", ephemeral=True)
        return

    if acusado.id == denunciante.id:
        await interaction.followup.send("No podes denunciarte a vos mismo.", ephemeral=True)
        return

    acusado_miembro = interaction.guild.get_member(acusado.id)

    if acusado_miembro and tiene_rol_protegido(acusado_miembro):
        await interaction.followup.send(f"No es posible denunciar a un usuario con el rol {ROL_PROTEGIDO}.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    denuncias_hechas = denuncias_por_usuario[guild_id][denunciante.id][acusado.id]

    if denuncias_hechas >= MAX_DENUNCIAS_POR_USUARIO:
        await interaction.followup.send(
            f"Ya alcanzaste el limite de {MAX_DENUNCIAS_POR_USUARIO} denuncia(s) contra este usuario.",
            ephemeral=True
        )
        return

    denuncias_por_usuario[guild_id][denunciante.id][acusado.id] += 1
    denuncias[guild_id][acusado.id] += 1
    total = denuncias[guild_id][acusado.id]

    await interaction.followup.send(
        "Denuncia registrada.",
        ephemeral=True
    )

    canal_publico = bot.get_channel(CANAL_CONFIGURADO_ID)
    if canal_publico is None or canal_publico.guild.id != guild_id:
        canal_publico = interaction.channel

    nivel_actual = obtener_nivel_actual(total)
    proximo = obtener_proximo_umbral(total)

    if nivel_actual is None:
        restantes = NIVELES[0]["umbral"] - total
        await canal_publico.send(
            f"ADVERTENCIA DE SEGURIDAD\n"
            f"Hola {acusado.mention}, se ha registrado una denuncia sobre uno de sus mensajes.\n"
            f"Es posible que este infringiendo las reglas de este servidor.\n\n"
            f"Denuncias acumuladas: {total}\n"
            f"Denuncias para primer aislamiento: {NIVELES[0]['umbral']}\n"
            f"Margen restante: {restantes} denuncia(s)."
        )
        return

    umbral_alcanzado = nivel_actual["umbral"]

    if total == umbral_alcanzado:
        limpiar_historial_expirado(guild_id, acusado.id)
        veces = contar_sanciones_acumuladas(guild_id, acusado.id, umbral_alcanzado) + 1
        duracion_final = nivel_actual["duracion"] * veces

        if duracion_final.total_seconds() > 2419200:
            duracion_final = datetime.timedelta(days=28)

        duracion_texto = formatear_duracion(duracion_final)
        expiracion = datetime.datetime.utcnow() + duracion_final
        historial_sanciones[guild_id][acusado.id].append({
            "umbral": umbral_alcanzado,
            "expira": expiracion
        })

        try:
            if acusado_miembro:
                await acusado_miembro.timeout(duracion_final, reason=f"Acumulacion de {total} denuncias.")
            await canal_publico.send(
                f"USUARIO AISLADO\n"
                f"{acusado.mention} ha sido aislado por {duracion_texto} "
                f"al alcanzar {total} denuncias.\n"
                f"Sancion numero {veces} en este nivel."
            )
        except discord.Forbidden:
            await canal_publico.send(f"Error: Sin permisos para aislar a {acusado.display_name}.")
        except Exception as e:
            print(f"Error: {e}")

    else:
        if proximo:
            restantes = proximo["umbral"] - total
            await canal_publico.send(
                f"ADVERTENCIA DE SEGURIDAD\n"
                f"{acusado.mention} acumula {total} denuncia(s).\n"
                f"Proximo aislamiento a las {proximo['umbral']} denuncias ({proximo['etiqueta']}).\n"
                f"Faltan {restantes} denuncia(s)."
            )
        else:
            await canal_publico.send(
                f"ADVERTENCIA DE SEGURIDAD\n"
                f"{acusado.mention} acumula {total} denuncia(s).\n"
                f"Ha superado todos los umbrales de sancion establecidos."
            )

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print(f"Fallo al iniciar: {e}")
        sys.exit(1)
