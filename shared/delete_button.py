"""
Discord delete button view — adds a trash button to bot messages.

Usage:
    await message.reply("Hello!", view=DeleteButtonView(message.author.id))

Only the original requester or the bot owner can delete the message.
"""

import discord

OWNER_ID = 000000000000000000


class DeleteButtonView(discord.ui.View):
    """A persistent view with a delete button for bot messages."""

    def __init__(self, requester_id: int | None = None):
        super().__init__(timeout=None)
        self._requester_id = requester_id

    @discord.ui.button(
        label="",
        emoji="\U0001f5d1",  # wastebasket emoji
        style=discord.ButtonStyle.secondary,
        custom_id="delete_bot_message",
    )
    async def delete_button(
        self, button: discord.ui.Button, interaction: discord.Interaction,
    ):
        # Allow the original requester or the owner to delete
        if (
            self._requester_id is None
            or interaction.user.id == self._requester_id
            or interaction.user.id == OWNER_ID
        ):
            try:
                await interaction.message.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to delete that message.",
                    ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                "Only the person who asked can delete this.",
                ephemeral=True,
            )
